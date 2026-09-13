"""Immutable explicit repair consent, permanent operation recovery and non-replayable lineage."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import ProposedChange, inspect_patch
from accessforge_domain.repair_requests import model_profile, validate
from accessforge_domain.timestamps import to_rfc3339_utc

from . import evaluations
from .diagnosis_requests import operation_digest


class RequestRefused(ValueError):
    pass


def authorize(
    conn: psycopg.Connection[Any], workspace_id: str, actor: str, *, write: bool = True
) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
        (workspace_id, actor),
    ).fetchone()
    required = {Permission.EVIDENCE_READ} | ({Permission.PROJECT_CONFIGURE} if write else set())
    if row is None or not required <= permissions_for(Role(row["role"])):
        raise RequestRefused("current repair request permission unavailable")


def _scope(
    conn: psycopg.Connection[Any], finding_id: str, diagnosis_id: str
) -> tuple[dict[str, Any], list[str], list[str]]:
    row = conn.execute(
        """
        SELECT d.payload,d.payload_digest,d.attempt_id,d.evaluation_digest,r.id AS run_id,
               r.manifest_digest,m.canonical_manifest,m.project_id,m.source_snapshot_id,
               s.tree_digest,s.commit_sha,surface.paths,surface.revision AS surface_revision
          FROM finding_diagnosis d
          JOIN finding f ON f.id=d.finding_id AND f.workspace_id=d.workspace_id
          JOIN run r ON r.id=f.run_id AND r.workspace_id=f.workspace_id AND r.id=d.run_id
          JOIN sealed_manifest m ON m.run_id=r.id AND m.manifest_digest=r.manifest_digest
               AND m.workspace_id=r.workspace_id AND m.project_id=r.project_id
          JOIN source_snapshot s ON s.id=m.source_snapshot_id AND s.workspace_id=m.workspace_id
               AND s.project_id=m.project_id
          JOIN project p ON p.id=m.project_id AND p.workspace_id=m.workspace_id
          JOIN project_repair_surface surface ON surface.project_id=p.id
               AND surface.workspace_id=p.workspace_id
         WHERE d.id=%s AND f.id=%s AND d.deleted_at IS NULL
           AND f.status IN ('CANDIDATE','REPRODUCED')
           AND r.status='COMPLETED' AND r.outcome IN ('FAIL','INCONCLUSIVE')
           AND p.revoked_at IS NULL AND p.repository_authorized_by IS NOT NULL AND NOT s.dirty
           AND s.dirty_path_count=0
           AND NOT EXISTS(SELECT 1 FROM finding_diagnosis child WHERE child.supersedes=d.id)
           AND NOT EXISTS(SELECT 1 FROM evidence_artifact a
               WHERE a.attempt_id=d.attempt_id AND a.retention='DELETED')
         FOR SHARE OF d,f,r,s,p,surface
        """,
        (diagnosis_id, finding_id),
    ).fetchone()
    if row is None:
        raise LookupError("repair scope unavailable")
    if row["payload"] is None or digest(row["payload"]) != row["payload_digest"]:
        raise RequestRefused("current retained diagnosis and authorized baseline unavailable")
    evaluation = evaluations.read(conn, run_id=str(row["run_id"]))
    if (
        evaluation is None
        or evaluation["snapshotDigest"] != row["evaluation_digest"]
        or evaluation["snapshot"]["attemptId"] != str(row["attempt_id"])
        or digest(row["canonical_manifest"]) != row["manifest_digest"]
        or row["canonical_manifest"].get("sourceTreeDigest") != row["tree_digest"]
        or row["canonical_manifest"].get("sourceCommitSha") != row["commit_sha"]
    ):
        raise RequestRefused("original diagnosis evaluation/source identity unavailable")
    analysis = row["payload"]
    brief = analysis.get("repair_brief")
    if (
        analysis.get("support") != "SOURCE_LINKED"
        or not isinstance(brief, dict)
        or brief.get("stop_recommendation") is not None
    ):
        raise RequestRefused("supported non-stopped repair brief required")
    files = brief.get("allowed_files")
    if (
        not isinstance(files, list)
        or not 1 <= len(files) <= 20
        or not all(isinstance(path, str) for path in files)
        or len(set(files)) != len(files)
    ):
        raise RequestRefused("bounded exact repair paths required")
    inspection = inspect_patch(
        tuple(ProposedChange(path, "") for path in files), application_paths=tuple(row["paths"])
    )
    if not row["paths"] or not inspection.acceptable:
        raise RequestRefused("repair brief is outside current allowed application paths")
    return (
        {
            "diagnosisId": diagnosis_id,
            "diagnosisDigest": row["payload_digest"],
            "manifestDigest": row["manifest_digest"],
            "sourceTreeDigest": row["tree_digest"],
            "evaluationDigest": row["evaluation_digest"],
            "projectId": str(row["project_id"]),
            "sourceSnapshotId": str(row["source_snapshot_id"]),
            "repairSurfaceDigest": digest(
                {"paths": list(row["paths"]), "revision": int(row["surface_revision"])}
            ),
            "modelProfileDigest": digest(model_profile()),
        },
        [r.path for r in inspection.separately_reviewed],
        files,
    )


def preview(conn: psycopg.Connection[Any], *, finding_id: str, diagnosis_id: str) -> dict[str, Any]:
    scope, separate, files = _scope(conn, finding_id, diagnosis_id)
    latest = conn.execute(
        "SELECT q.id FROM repair_request q WHERE q.finding_id=%s "
        "AND NOT EXISTS(SELECT 1 FROM repair_request child WHERE child.supersedes=q.id)",
        (finding_id,),
    ).fetchone()
    return {
        "scope": {
            **scope,
            "supersedes": None if latest is None else str(latest["id"]),
            "billableCallAcknowledged": False,
            "separateReviewAcknowledged": False,
        },
        "separatelyReviewedPaths": separate,
        "sourcePaths": files,
        "profile": model_profile(),
        "disclosure": "Complete allowed source files and retained diagnosis are disclosed to this "
        "provider on explicit dispatch. Calls may be billable; "
        "token reservations are not a financial cap.",
        "meaning": "PREVIEW_NOT_CONSENT_OR_MODEL_INVOCATION",
    }


def _view(row: dict[str, Any]) -> dict[str, Any]:
    if digest(row["payload"]) != row["payload_digest"]:
        raise RequestRefused("repair request integrity unavailable")
    return {
        "requestId": str(row["id"]),
        "findingId": str(row["finding_id"]),
        "requestedBy": str(row["requested_by"]),
        "scope": row["payload"],
        "scopeDigest": row["payload_digest"],
        "createdAt": to_rfc3339_utc(row["created_at"]),
        "expiresAt": to_rfc3339_utc(row["expires_at"]),
        "revokedAt": None if row["revoked_at"] is None else to_rfc3339_utc(row["revoked_at"]),
        "meaning": "HUMAN_REQUEST_NOT_PROPOSAL_APPROVAL_OR_MODEL_COMPLETION",
        "deliveryMode": "EXPLICIT_OPERATOR_DISPATCH",
    }


def inspect(conn: psycopg.Connection[Any], *, request_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM repair_request WHERE id=%s", (request_id,)).fetchone()
    if row is None:
        raise LookupError("repair request unavailable")
    invocation = conn.execute(
        "SELECT status FROM diagnosis_invocation WHERE operation_id=%s AND purpose='REPAIR'",
        (request_id,),
    ).fetchone()
    return {
        **_view(row),
        "invocationState": "NOT_STARTED" if invocation is None else invocation["status"],
    }


def create(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    finding_id: str,
    requested_by: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    validate(payload)
    authorize(conn, workspace_id, requested_by)
    key = operation_digest(idempotency_key)
    # Serialize a finding's lineage, including distinct-key concurrent requests.
    conn.execute("SELECT id FROM finding WHERE id=%s FOR UPDATE", (finding_id,))
    previous = conn.execute(
        "SELECT * FROM repair_request WHERE workspace_id=%s AND finding_id=%s AND requested_by=%s "
        "AND operation_key_digest=%s",
        (workspace_id, finding_id, requested_by, key),
    ).fetchone()
    if previous is not None:
        if previous["payload_digest"] != digest(payload):
            raise RequestRefused("operation identity cannot be reused for another repair scope")
        return inspect(conn, request_id=str(previous["id"]))
    current = preview(conn, finding_id=finding_id, diagnosis_id=payload["diagnosisId"])
    expected = {
        **current["scope"],
        "billableCallAcknowledged": True,
        "separateReviewAcknowledged": payload["separateReviewAcknowledged"],
    }
    if payload != expected or (
        current["separatelyReviewedPaths"] and not payload["separateReviewAcknowledged"]
    ):
        raise RequestRefused(
            "repair scope changed or separate dependency/build review was not acknowledged"
        )
    parent = payload["supersedes"]
    if parent is not None:
        old = conn.execute(
            "SELECT q.*,i.status AS invocation_status FROM repair_request q "
            "LEFT JOIN diagnosis_invocation i "
            "ON i.operation_id=q.id AND i.workspace_id=q.workspace_id AND i.purpose='REPAIR' "
            "WHERE q.id=%s",
            (parent,),
        ).fetchone()
        if old is None or old["invocation_status"] in {"STARTED", "UNCONFIRMED"}:
            raise RequestRefused("uncertain prior invocation must be reconciled, never bypassed")
        if (
            old["invocation_status"] is None
            and old["revoked_at"] is None
            and conn.execute(
                "SELECT 1 FROM repair_request WHERE id=%s AND expires_at>clock_timestamp()",
                (parent,),
            ).fetchone()
        ):
            raise RequestRefused("an existing unstarted request is still active")
    row = conn.execute(
        "INSERT INTO repair_request(id,workspace_id,finding_id,diagnosis_id,requested_by,"
        "payload,payload_digest,operation_key_digest,supersedes) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            str(uuid4()),
            workspace_id,
            finding_id,
            payload["diagnosisId"],
            requested_by,
            Jsonb(payload),
            digest(payload),
            key,
            parent,
        ),
    ).fetchone()
    assert row is not None
    return inspect(conn, request_id=str(row["id"]))


def recover(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    finding_id: str,
    actor_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id FROM repair_request WHERE workspace_id=%s AND finding_id=%s AND requested_by=%s "
        "AND operation_key_digest=%s",
        (workspace_id, finding_id, actor_id, operation_digest(idempotency_key)),
    ).fetchone()
    if row is None:
        raise LookupError("repair acceptance not confirmed for this operation")
    return inspect(conn, request_id=str(row["id"]))


def require_active(conn: psycopg.Connection[Any], *, request_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM repair_request WHERE id=%s AND revoked_at IS NULL "
        "AND expires_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM repair_request child "
        "WHERE child.supersedes=repair_request.id) FOR SHARE",
        (request_id,),
    ).fetchone()
    if row is None:
        raise RequestRefused("repair consent expired, revoked, superseded or unavailable")
    authorize(conn, str(row["workspace_id"]), str(row["requested_by"]))
    result = _view(row)
    validate(result["scope"])
    current, separate, _ = _scope(conn, str(row["finding_id"]), str(row["diagnosis_id"]))
    if any(result["scope"][key] != value for key, value in current.items()) or (
        separate and result["scope"]["separateReviewAcknowledged"] is not True
    ):
        raise RequestRefused("repair source/diagnosis/authority changed after consent")
    return result


def revoke(
    conn: psycopg.Connection[Any], *, workspace_id: str, request_id: str, actor_id: str
) -> dict[str, Any]:
    authorize(conn, workspace_id, actor_id, write=False)
    current = inspect(conn, request_id=request_id)
    if current["requestedBy"] != actor_id:
        owner = conn.execute(
            "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s",
            (workspace_id, actor_id),
        ).fetchone()
        if owner is None or owner["role"] != "OWNER":
            raise RequestRefused("only requester or workspace owner may revoke repair consent")
    conn.execute(
        "UPDATE repair_request SET revoked_at=clock_timestamp() WHERE id=%s AND revoked_at IS NULL",
        (request_id,),
    )
    return inspect(conn, request_id=request_id)
