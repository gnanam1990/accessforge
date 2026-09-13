"""Trusted diagnosis delivery: authorize/read -> bounded model -> recheck -> immutable occurrence.

Calling deliver with real provider credentials is billable. It is never invoked by a GET route,
import, app startup or background default; an operator-owned workflow must explicitly request it.
"""

from __future__ import annotations

from threading import Event
from typing import Any

from accessforge_domain.canonical import digest
from accessforge_domain.diagnosis_requests import model_profile
from accessforge_orchestrator.execution_artifacts import ExecutionArtifactStore, Refused
from accessforge_persistence import (
    diagnoses,
    diagnosis_invocations,
    diagnosis_requests,
    workspace_connection,
)

from .agent import DiagnosisAgentProfile, DiagnosisWorker, build_diagnosis_agent
from .projection import ExcerptRequest, prepare
from .source import FrozenSourceScope


async def deliver(
    database_url: str,
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    requested_by: str,
    operation_id: str,
    assertion_id: str,
    component_path: str,
    component_name: str,
    source_scope: FrozenSourceScope,
    excerpts: tuple[ExcerptRequest, ...],
    profile: DiagnosisAgentProfile,
    supersedes: str | None = None,
    cancel_signal: Event | None = None,
    authorized_request_id: str | None = None,
) -> dict[str, Any]:
    fence = cancel_signal or Event()
    if fence.is_set() or component_path not in {item.path for item in excerpts}:
        raise Refused("diagnosis cancelled or component outside the supplied source excerpts")
    profile_digest = digest(profile.model_dump(mode="json"))

    def check_request(conn: Any, evaluation_digest: str, manifest_digest: str) -> None:
        if authorized_request_id is None:
            return  # Internal operator-owned callers predate the public request workflow.
        decision = diagnosis_requests.require_active(
            conn,
            workspace_id=workspace_id,
            request_id=authorized_request_id,
        )
        expected = {
            "manifestDigest": manifest_digest,
            "evaluationDigest": evaluation_digest,
            "modelProfileDigest": profile_digest,
            "assertionId": assertion_id,
            "componentPath": component_path,
            "componentName": component_name,
            "excerpts": [
                {"path": item.path, "lineStart": item.line_start, "lineEnd": item.line_end}
                for item in excerpts
            ],
            "supersedes": supersedes,
            "billableCallAcknowledged": True,
        }
        if (
            operation_id != authorized_request_id
            or decision["runId"] != run_id
            or decision["requestedBy"] != requested_by
            or decision["scope"] != expected
        ):
            raise Refused("diagnosis worker inputs differ from the human request")

    with workspace_connection(database_url, workspace_id) as conn:
        diagnoses.authorize(conn, workspace_id, requested_by)
        before = prepare(
            conn,
            store,
            workspace_id=workspace_id,
            run_id=run_id,
            assertion_id=assertion_id,
            component_name=component_name,
            source_scope=source_scope,
            excerpts=excerpts,
        )
        if assertion_id not in {item.assertion_id for item in before.projection.assertions}:
            raise Refused("requested diagnosis assertion is outside the original evaluation")
        check_request(conn, before.evaluation_digest, before.manifest_digest)
        identity = diagnoses.request_digest(
            workspace_id=workspace_id,
            run_id=run_id,
            requested_by=requested_by,
            assertion_id=assertion_id,
            component_identity=component_path,
            projection_digest=before.projection_digest,
            model_profile_digest=profile_digest,
            supersedes=supersedes,
        )
        existing = diagnoses.by_operation(
            conn, workspace_id=workspace_id, operation_id=operation_id
        )
        if existing is not None:
            if existing["requestDigest"] != identity:
                raise Refused("diagnosis operation was already used for different inputs")
            return existing
        diagnosis_invocations.reserve(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            operation_id=operation_id,
            request_digest=identity,
            tokens=profile.invocation_total_tokens,
        )
    # The STARTED reservation commits before any provider work. A process crash or uncertain
    # commit leaves an operation that must be reconciled, never automatically invoked again.
    provider_possible = False

    def provider_entered() -> None:
        nonlocal provider_possible
        provider_possible = True

    try:
        if fence.is_set():
            raise Refused("diagnosis cancelled before model invocation")
        worker = DiagnosisWorker(
            profile=profile, agent_builder=lambda _: build_diagnosis_agent(profile)
        )
        result = await worker.diagnose(
            before.projection,
            cancel_signal=fence,
            on_provider_invoke=provider_entered,
        )
        if fence.is_set():
            raise Refused("diagnosis was interrupted; no finding created")
        # No database locks span the provider call. Recheck source, retention and permission.
        with workspace_connection(database_url, workspace_id) as conn:
            diagnoses.authorize(conn, workspace_id, requested_by)
            # Serialize before artifact locks, avoiding a concurrent shared-run lock upgrade.
            conn.execute(
                "SELECT id FROM run WHERE id=%s AND workspace_id=%s FOR UPDATE",
                (run_id, workspace_id),
            )
            after = prepare(
                conn,
                store,
                workspace_id=workspace_id,
                run_id=run_id,
                assertion_id=assertion_id,
                component_name=component_name,
                source_scope=source_scope,
                excerpts=excerpts,
            )
            if (
                fence.is_set()
                or before.evaluation_digest != after.evaluation_digest
                or before.projection_digest != after.projection_digest
            ):
                raise Refused("diagnosis inputs changed during model work; no finding created")
            check_request(conn, after.evaluation_digest, after.manifest_digest)
            retained = diagnoses.retain(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                requested_by=requested_by,
                operation_id=operation_id,
                assertion_id=assertion_id,
                component_identity=component_path,
                evaluation_digest=after.evaluation_digest,
                projection_digest=after.projection_digest,
                model_profile_digest=profile_digest,
                analysis=result.model_dump(mode="json"),
                supersedes=supersedes,
            )
            diagnosis_invocations.finish(
                conn,
                workspace_id=workspace_id,
                operation_id=operation_id,
                request_digest=identity,
                status="RECORDED" if provider_possible else "NOT_CALLED",
            )
        return retained
    except BaseException as exc:
        fence.set()
        try:
            with workspace_connection(database_url, workspace_id) as conn:
                diagnosis_invocations.finish(
                    conn,
                    workspace_id=workspace_id,
                    operation_id=operation_id,
                    request_digest=identity,
                    status="UNCONFIRMED" if provider_possible else "NOT_CALLED",
                )
        except Exception:
            # Lost commit acknowledgement may mean RECORDED already committed. Never overwrite
            # it. If storage is unavailable, the durable STARTED hold still prevents replay.
            exc.add_note("Invocation settlement unavailable; reconcile the original operation.")
        raise


async def deliver_requested(
    database_url: str,
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    request_id: str,
    source_scope: FrozenSourceScope,
    cancel_signal: Event | None = None,
) -> dict[str, Any]:
    """Explicit worker entry point. Provider credentials and source scope belong to the host.

    The caller cannot choose a requester, run, provider, source excerpt or output status. Those
    come from the authenticated immutable decision, with current membership and scope rechecks.
    This is potentially billable and is never called by request API handlers or app startup.
    """
    with workspace_connection(database_url, workspace_id) as conn:
        decision = diagnosis_requests.require_active(
            conn,
            workspace_id=workspace_id,
            request_id=request_id,
        )
    body = decision["scope"]
    return await deliver(
        database_url,
        store,
        workspace_id=workspace_id,
        run_id=decision["runId"],
        requested_by=decision["requestedBy"],
        operation_id=request_id,
        assertion_id=body["assertionId"],
        component_path=body["componentPath"],
        component_name=body["componentName"],
        source_scope=source_scope,
        excerpts=tuple(
            ExcerptRequest(item["path"], item["lineStart"], item["lineEnd"])
            for item in body["excerpts"]
        ),
        profile=DiagnosisAgentProfile.model_validate(model_profile()),
        supersedes=body["supersedes"],
        cancel_signal=cancel_signal,
        authorized_request_id=request_id,
    )
