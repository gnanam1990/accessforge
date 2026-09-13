"""Immutable diagnosis occurrences; model analysis cannot rewrite run outcomes or human reviews."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import canonicalize, digest
from accessforge_domain.states import FindingStatus, Outcome
from accessforge_domain.timestamps import to_rfc3339_utc

from . import evaluations, reviews


class DiagnosisRefused(ValueError):
    pass


def request_digest(
    *,
    workspace_id: str,
    run_id: str,
    requested_by: str,
    assertion_id: str,
    component_identity: str,
    projection_digest: str,
    model_profile_digest: str,
    supersedes: str | None,
) -> str:
    return digest(
        {
            "workspaceId": workspace_id,
            "runId": run_id,
            "requestedBy": requested_by,
            "assertionId": assertion_id,
            "componentIdentity": component_identity,
            "projectionDigest": projection_digest,
            "modelProfileDigest": model_profile_digest,
            "supersedes": supersedes,
        }
    )


def authorize(conn: psycopg.Connection[Any], workspace_id: str, requested_by: str) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
        (workspace_id, requested_by),
    ).fetchone()
    if row is None or not {Permission.RUN_REQUEST, Permission.EVIDENCE_READ} <= permissions_for(
        Role(row["role"])
    ):
        raise DiagnosisRefused(
            "diagnosis requester no longer holds run-request and evidence-read permission"
        )


def _view(row: dict[str, Any]) -> dict[str, Any]:
    if row["payload"] is not None and digest(row["payload"]) != row["payload_digest"]:
        raise DiagnosisRefused("original diagnosis content integrity unavailable")
    return {
        "diagnosisId": str(row["id"]),
        "requestDigest": row["request_digest"],
        "findingId": str(row["finding_id"]),
        "runId": str(row["run_id"]),
        "groupDigest": row["group_digest"],
        "evaluationDigest": row["evaluation_digest"],
        "projectionDigest": row["projection_digest"],
        "modelProfileDigest": row["model_profile_digest"],
        "payloadDigest": row["payload_digest"],
        "analysis": row["payload"],
        "requestedBy": str(row["requested_by"]),
        "establishedBy": "MODEL_HYPOTHESIS_NOT_MACHINE_VERDICT",
        "supersedes": None if row["supersedes"] is None else str(row["supersedes"]),
        "recordedAt": to_rfc3339_utc(row["created_at"]),
        "deletedAt": None if row["deleted_at"] is None else to_rfc3339_utc(row["deleted_at"]),
    }


def retain(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    requested_by: str,
    operation_id: str,
    assertion_id: str,
    component_identity: str,
    evaluation_digest: str,
    projection_digest: str,
    model_profile_digest: str,
    analysis: dict[str, Any],
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Trusted post-validator write; caller must revalidate artifact/source inputs before this call.

    Group by exact project/journey/assertion/component; keep one finding per run occurrence.
    New analysis of that occurrence requires an explicit predecessor. It never changes its existing
    finding status or summary and never promotes a model's support label into a run verdict.
    """
    for value in (workspace_id, run_id, requested_by, operation_id):
        if str(UUID(value)) != value:
            raise DiagnosisRefused("canonical diagnosis identity required")
    if (
        not component_identity
        or len(component_identity) > 500
        or len(canonicalize(analysis)) > 60000
    ):
        raise DiagnosisRefused("bounded component and diagnosis content required")
    authorize(conn, workspace_id, requested_by)
    run = conn.execute(
        "SELECT * FROM run WHERE id=%s AND workspace_id=%s FOR UPDATE", (run_id, workspace_id)
    ).fetchone()
    original = evaluations.read(conn, run_id=run_id)
    if (
        run is None
        or original is None
        or run["status"] != "COMPLETED"
        or original["snapshotDigest"] != evaluation_digest
        or run["outcome"] != original["snapshot"]["outcome"]
        or run["outcome"] not in {"FAIL", "INCONCLUSIVE"}
    ):
        raise DiagnosisRefused("original diagnosis evaluation unavailable")
    assertion = next(
        (
            item
            for item in original["snapshot"]["assertions"]
            if item["assertionId"] == assertion_id
        ),
        None,
    )
    if assertion is None:
        raise DiagnosisRefused("assertion does not belong to the original evaluation")
    sealed = conn.execute(
        "SELECT canonical_manifest FROM sealed_manifest WHERE run_id=%s", (run_id,)
    ).fetchone()
    if sealed is None or digest(sealed["canonical_manifest"]) != run["manifest_digest"]:
        raise DiagnosisRefused("original diagnosis manifest unavailable")
    manifest = sealed["canonical_manifest"]
    group = digest(
        {
            "projectId": manifest["projectId"],
            "journeyDigest": manifest["journeyDigest"],
            "assertionSetDigest": manifest["assertionSetDigest"],
            "assertionId": assertion_id,
            "component": component_identity,
        }
    )
    payload_digest = digest(analysis)
    request_identity = request_digest(
        workspace_id=workspace_id,
        run_id=run_id,
        requested_by=requested_by,
        assertion_id=assertion_id,
        component_identity=component_identity,
        projection_digest=projection_digest,
        model_profile_digest=model_profile_digest,
        supersedes=supersedes,
    )
    previous = conn.execute(
        "SELECT * FROM finding_diagnosis WHERE operation_id=%s AND workspace_id=%s",
        (operation_id, workspace_id),
    ).fetchone()
    if previous is not None:
        if any(
            (
                str(previous["run_id"]) != run_id,
                previous["group_digest"] != group,
                previous["payload_digest"] != payload_digest,
                previous["projection_digest"] != projection_digest,
                previous["evaluation_digest"] != evaluation_digest,
                previous["model_profile_digest"] != model_profile_digest,
                previous["request_digest"] != request_identity,
                str(previous["requested_by"]) != requested_by,
                (None if previous["supersedes"] is None else str(previous["supersedes"]))
                != supersedes,
            )
        ):
            raise DiagnosisRefused("diagnosis operation identity was reused for different content")
        return _view(previous)
    existing = conn.execute(
        "SELECT finding_id FROM finding_diagnosis WHERE run_id=%s AND group_digest=%s LIMIT 1",
        (run_id, group),
    ).fetchone()
    if existing is None:
        if supersedes is not None:
            raise DiagnosisRefused("diagnosis predecessor unavailable")
        status = (
            FindingStatus.REPRODUCED
            if (
                run["outcome"] == "FAIL"
                and assertion["condition"] == "FALSE"
                and analysis.get("support") == "SOURCE_LINKED"
            )
            else FindingStatus.CANDIDATE
        )
        # Generic summary has no model-derived text to survive later evidence erasure.
        created = reviews.create_finding(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            assertion_id=assertion_id,
            summary="Retained diagnosis for a frozen journey assertion",
            status=status,
            run_outcome=Outcome(run["outcome"]),
            actor_id=requested_by,
        )
        # Keep one finding per run occurrence, not a mutable model-summary match.
        finding_id = created
    else:
        finding_id = str(existing["finding_id"])
        predecessor = conn.execute(
            "SELECT id FROM finding_diagnosis WHERE id=%s AND finding_id=%s",
            (supersedes, finding_id),
        ).fetchone()
        if predecessor is None:
            raise DiagnosisRefused("follow-up diagnosis requires its exact predecessor")
    row = conn.execute(
        "INSERT INTO finding_diagnosis(id,workspace_id,finding_id,run_id,attempt_id,operation_id,"
        "requested_by,group_digest,evaluation_digest,projection_digest,model_profile_digest,request_digest,"
        "payload_digest,payload,supersedes) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            str(uuid4()),
            workspace_id,
            finding_id,
            run_id,
            original["snapshot"]["attemptId"],
            operation_id,
            requested_by,
            group,
            evaluation_digest,
            projection_digest,
            model_profile_digest,
            request_identity,
            payload_digest,
            Jsonb(analysis),
            supersedes,
        ),
    ).fetchone()
    assert row is not None
    return _view(row)


def history(conn: psycopg.Connection[Any], *, finding_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM finding_diagnosis WHERE finding_id=%s "
        "ORDER BY created_at DESC,id DESC LIMIT 51",
        (finding_id,),
    ).fetchall()
    return {"items": [_view(row) for row in rows[:50]], "complete": len(rows) <= 50}


def by_operation(
    conn: psycopg.Connection[Any], *, workspace_id: str, operation_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM finding_diagnosis WHERE workspace_id=%s AND operation_id=%s",
        (workspace_id, operation_id),
    ).fetchone()
    return None if row is None else _view(row)
