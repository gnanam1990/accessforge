"""Explicit billable delivery of an authenticated repair request. Never imported as a dispatcher."""

from collections.abc import Mapping
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.repair_requests import model_profile
from accessforge_orchestrator.execution_artifacts import ExecutionArtifactStore, Refused
from accessforge_persistence import (
    diagnosis_invocations,
    patches,
    repair_deliveries,
    repair_requests,
    workspace_connection,
)

from .projection import PreparedRepairInput, prepare
from .worker import RepairAgentProfile, RepairWorker, build_repair_agent


def _prepared(
    conn: psycopg.Connection[Any],
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    decision: dict[str, Any],
    repositories: Mapping[str, Path],
    fence: Event,
) -> PreparedRepairInput:
    # Only metadata read before preparation. Artifacts lock before the current diagnosis.
    prepared = prepare(
        conn,
        store,
        workspace_id=workspace_id,
        finding_id=decision["findingId"],
        diagnosis_id=decision["scope"]["diagnosisId"],
        requested_by=decision["requestedBy"],
        repositories=repositories,
        cancelled=fence.is_set,
    )
    active = repair_requests.require_active(conn, request_id=decision["requestId"])
    if (
        active["scopeDigest"] != decision["scopeDigest"]
        or active["requestedBy"] != prepared.requested_by
    ):
        raise Refused("repair request identity changed")
    expected = {
        "diagnosisId": prepared.inputs.diagnosis_id,
        "diagnosisDigest": prepared.inputs.diagnosis_digest,
        "manifestDigest": prepared.inputs.base_manifest_digest,
        "sourceTreeDigest": prepared.inputs.source.tree_digest,
        "evaluationDigest": prepared.evaluation_digest,
        "projectId": prepared.project_id,
        "sourceSnapshotId": prepared.source_snapshot_id,
        "repairSurfaceDigest": digest(
            {
                "paths": list(prepared.inputs.application_paths),
                "revision": prepared.surface_revision,
            }
        ),
        "modelProfileDigest": digest(model_profile()),
        "billableCallAcknowledged": True,
    }
    if fence.is_set() or any(active["scope"][key] != value for key, value in expected.items()):
        raise Refused("prepared source/diagnosis differs from explicitly authorized scope")
    return prepared


async def deliver_requested(
    database_url: str,
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    request_id: str,
    repositories: Mapping[str, Path],
    cancel_signal: Event | None = None,
) -> dict[str, Any]:
    """Private operator entry. No caller-controlled actor, source identity, model or output patch.

    Requires stored human consent plus operator invocation. This code is potentially billable;
    neither request API handlers nor startup/background defaults call it.
    """
    if any(str(UUID(value)) != value for value in (workspace_id, request_id)):
        raise Refused("canonical repair operation identity required")
    fence = cancel_signal or Event()
    profile = RepairAgentProfile.model_validate(model_profile())
    with workspace_connection(database_url, workspace_id) as conn:
        decision = repair_requests.inspect(conn, request_id=request_id)
        repair_requests.authorize(conn, workspace_id, decision["requestedBy"], write=False)
        if decision["delivery"] is not None:
            prior = decision["delivery"]
            if not isinstance(prior, dict):
                raise Refused("repair delivery readback shape unavailable")
            return prior  # Readback is not renewed execution permission.
        if decision["invocationState"] != "NOT_STARTED":
            raise Refused("repair invocation already exists; reconcile the original operation")
        before = _prepared(
            conn,
            store,
            workspace_id=workspace_id,
            decision=decision,
            repositories=repositories,
            fence=fence,
        )
        input_digest = digest(
            {
                "repairInput": before.inputs.model_dump(mode="json"),
                "modelProfile": profile.model_dump(mode="json"),
            }
        )
        identity = digest(
            {
                "requestId": request_id,
                "scopeDigest": decision["scopeDigest"],
                "bindingDigest": before.binding_digest,
                "inputDigest": input_digest,
            }
        )
        diagnosis_invocations.reserve(
            conn,
            workspace_id=workspace_id,
            run_id=before.run_id,
            operation_id=request_id,
            request_digest=identity,
            tokens=profile.invocation_total_tokens,
            purpose="REPAIR",
        )
    # Durable STARTED commits before any model construction. A lost reservation commit response
    # stops here and cannot fall through to invocation. No database locks span the provider call.
    provider_possible = False

    def entered() -> None:
        nonlocal provider_possible
        provider_possible = True

    try:
        if fence.is_set():
            raise Refused("repair cancelled before provider work")
        result = await RepairWorker(
            profile=profile, agent_builder=lambda _: build_repair_agent(profile)
        ).propose(before.inputs, cancel_signal=fence, on_provider_invoke=entered)
        if fence.is_set() or result.input_digest != input_digest:
            raise Refused("cancelled repair or result bound to other input")
        with workspace_connection(database_url, workspace_id) as conn:
            after = _prepared(
                conn,
                store,
                workspace_id=workspace_id,
                decision=decision,
                repositories=repositories,
                fence=fence,
            )
            if after.binding_digest != before.binding_digest:
                raise Refused(
                    "repair source, retained evidence or authority changed during model work"
                )
            patch = None
            if result.status == "PROPOSAL_READY":
                if (
                    not provider_possible
                    or result.patch_digest != patches.patch_digest(result.changes)
                    or result.rationale is None
                    or result.uncertainty is None
                ):
                    raise Refused("reviewable complete model draft unavailable")
                patch = patches.propose_patch(
                    conn,
                    workspace_id=workspace_id,
                    finding_id=decision["findingId"],
                    base_manifest_digest=after.inputs.base_manifest_digest,
                    base_source_digest=after.inputs.source.tree_digest,
                    changes=result.changes,
                    rationale=result.rationale
                    + "\n\nModel uncertainty (not verification): "
                    + result.uncertainty,
                    proposed_by=decision["requestedBy"],
                    acknowledge_separate_review=decision["scope"]["separateReviewAcknowledged"],
                )
            elif (
                result.status != "UNAVAILABLE" or result.changes or result.patch_digest is not None
            ):
                raise Refused("unexpected repair worker result")
            if fence.is_set():
                raise Refused("repair cancelled before atomic proposal settlement")
            repair_deliveries.record(
                conn,
                workspace_id=workspace_id,
                request_id=request_id,
                request_digest=identity,
                input_digest=input_digest,
                binding_digest=after.binding_digest,
                patch=patch,
            )
            diagnosis_invocations.finish(
                conn,
                workspace_id=workspace_id,
                operation_id=request_id,
                request_digest=identity,
                purpose="REPAIR",
                status="RECORDED" if provider_possible else "NOT_CALLED",
            )
            retained = repair_deliveries.by_request(conn, request_id=request_id)
            if retained is None:
                raise Refused("repair settlement readback unavailable")
        return retained
    except BaseException as exc:
        fence.set()
        try:
            with workspace_connection(database_url, workspace_id) as conn:
                diagnosis_invocations.finish(
                    conn,
                    workspace_id=workspace_id,
                    operation_id=request_id,
                    request_digest=identity,
                    purpose="REPAIR",
                    status="UNCONFIRMED" if provider_possible else "NOT_CALLED",
                )
        except Exception:
            # RECORDED may already have committed. Never overwrite it or invoke again to recover.
            exc.add_note(
                "Repair settlement unconfirmed; read the original request before further action."
            )
        raise
