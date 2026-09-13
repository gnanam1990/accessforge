"""Prepare diagnosis from retained verifier artifacts and a measured authorized frozen source.

No model call, finding status, source mutation or execution authority is produced here. The host
supplies a private authorized source scope; every requested excerpt must belong to that scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

from accessforge_domain.canonical import digest
from accessforge_orchestrator.execution_artifacts import ExecutionArtifactStore, Refused
from accessforge_orchestrator.finalize_execution import _retained
from accessforge_persistence import evaluations, journeys
from accessforge_persistence.source_intake import assert_reproducible, resolve_source

from .models import DiagnosisProjection, EvidenceReference, ProtectedAssertion, SourceIdentity
from .source import FrozenSourceReader, FrozenSourceScope


@dataclass(frozen=True)
class ExcerptRequest:
    path: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class PreparedDiagnosis:
    evaluation_id: str
    evaluation_digest: str
    manifest_digest: str
    projection_digest: str
    projection: DiagnosisProjection


def _assert_source(scope: FrozenSourceScope, manifest: dict[str, Any]) -> None:
    identity = resolve_source(scope.root)
    assert_reproducible(identity)
    if (
        identity.commit_sha != scope.commit_sha
        or identity.commit_sha != manifest["sourceCommitSha"]
        or identity.tree_digest != manifest["sourceTreeDigest"]
    ):
        raise Refused("diagnosis source does not match the original sealed commit and tree")


def prepare(
    conn: psycopg.Connection[Any],
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    assertion_id: str,
    component_name: str,
    source_scope: FrozenSourceScope,
    excerpts: tuple[ExcerptRequest, ...],
) -> PreparedDiagnosis:
    """Read under the caller's workspace transaction; refuse incomplete or changed retained input.

    Inconclusive evaluations remain inconclusive in the projection. Unknown reader capture is
    never converted into a silent announcement or an observed product failure. Event text comes
    from the retained producer stream, never from caller-supplied diagnosis JSON.
    """
    for value in (workspace_id, run_id):
        if str(UUID(value)) != value:
            raise Refused("canonical diagnosis workspace/run identifiers required")
    if not 1 <= len(excerpts) <= 40:
        raise Refused("one to forty scoped source excerpts required")
    run = conn.execute(
        "SELECT * FROM run WHERE id=%s AND workspace_id=%s FOR SHARE", (run_id, workspace_id)
    ).fetchone()
    original = evaluations.read(conn, run_id=run_id)
    if (
        run is None
        or original is None
        or run["status"] != "COMPLETED"
        or run["outcome"] not in {"FAIL", "INCONCLUSIVE"}
        or run["outcome"] != original["snapshot"]["outcome"]
        or run["manifest_digest"] != original["snapshot"]["manifestDigest"]
    ):
        raise Refused("diagnosis requires an original completed FAIL or INCONCLUSIVE evaluation")
    sealed = conn.execute(
        "SELECT canonical_manifest FROM sealed_manifest WHERE run_id=%s", (run_id,)
    ).fetchone()
    if sealed is None or not isinstance(sealed["canonical_manifest"], dict):
        raise Refused("diagnosis manifest unavailable")
    manifest = sealed["canonical_manifest"]
    if digest(manifest) != run["manifest_digest"]:
        raise Refused("diagnosis manifest content differs")
    ticket = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE run_id=%s AND workspace_id=%s FOR SHARE",
        (run_id, workspace_id),
    ).fetchone()
    if (
        ticket is None
        or str(ticket["attempt_id"]) != original["snapshot"]["attemptId"]
        or ticket["epoch"] != run["lease_epoch"]
        or ticket["accepted_at"] is None
    ):
        raise Refused("diagnosis attempt differs from original evaluation")
    row = {**ticket, "manifest_digest": run["manifest_digest"]}
    retained, artifact_ids = _retained(conn, store, row)
    if artifact_ids != original["snapshot"]["artifacts"]:
        raise Refused("diagnosis artifacts differ from original evaluation")
    contract = journeys.load_assertion_contract(
        conn,
        version_id=manifest["journeyVersionId"],
        expected_digest=manifest["assertionSetDigest"],
    )
    values = original["snapshot"].get("assertions")
    if not isinstance(values, list) or len(values) != len(contract.required):
        raise Refused("original assertion outcomes unavailable")
    outcomes = {item["assertionId"]: item for item in values}
    if len(outcomes) != len(values) or set(outcomes) != {a.assertion_id for a in contract.required}:
        raise Refused("original assertion identities differ")
    if assertion_id not in outcomes:
        raise Refused("selected assertion is outside the original evaluation")
    assertions = tuple(
        ProtectedAssertion(
            assertion_id=assertion.assertion_id,
            description=assertion.description,
            condition=outcomes[assertion.assertion_id]["condition"],
        )
        for assertion in contract.required
        if assertion.assertion_id == assertion_id
    )
    evidence = [
        EvidenceReference(
            evidence_id=original["evaluationId"],
            kind="ASSERTION_RESULT",
            digest=original["snapshotDigest"],
            retained=True,
        )
    ]
    for event in retained["SPEECH_TRANSCRIPT"]["records"]:
        if len(evidence) >= 500:
            raise Refused("diagnosis reader evidence exceeds the bounded projection")
        payload = event["payload"]
        source = payload["sourceRecord"]
        if event["eventType"] != "READER_OBSERVATION" or payload["serviceIdentity"] != "SUPERVISOR":
            raise Refused("diagnosis reader provenance differs")
        phrase = source.get("phrase")
        redacted = payload.get("submittedSourceRecordDigest") != payload.get("sourceRecordDigest")
        known = (
            not redacted and source.get("status") != "CAPTURE_UNKNOWN" and isinstance(phrase, str)
        )
        if known and len(phrase) > 4000:
            raise Refused("reader observation exceeds diagnosis text budget")
        evidence.append(
            EvidenceReference(
                evidence_id=event["eventId"],
                kind="READER_OBSERVATION",
                digest=digest(event),
                retained=True,
                observed_text=phrase if known else None,
                observation_state="REDACTED"
                if redacted
                else "RECORDED"
                if known
                else "CAPTURE_UNKNOWN",
            )
        )
    _assert_source(source_scope, manifest)
    reader = FrozenSourceReader(source_scope)
    source_excerpts = tuple(
        reader.read(request.path, line_start=request.line_start, line_end=request.line_end)
        for request in excerpts
    )
    _assert_source(source_scope, manifest)
    projection = DiagnosisProjection(
        run_id=run_id,
        run_outcome=run["outcome"],
        component_name=component_name,
        source=SourceIdentity(
            commit_sha=manifest["sourceCommitSha"], tree_digest=manifest["sourceTreeDigest"]
        ),
        assertions=assertions,
        evidence=tuple(evidence),
        source_excerpts=source_excerpts,
    )
    return PreparedDiagnosis(
        evaluation_id=original["evaluationId"],
        evaluation_digest=original["snapshotDigest"],
        manifest_digest=run["manifest_digest"],
        projection_digest=digest(projection.model_dump(mode="json")),
        projection=projection,
    )
