"""Authenticated, expiring human requests; stored approval is not worker or model success."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest
from accessforge_domain.diagnosis_requests import validate
from accessforge_domain.timestamps import to_rfc3339_utc

from . import diagnoses, evaluations


class RequestRefused(ValueError):
    pass


def _view(row: dict[str, Any]) -> dict[str, Any]:
    if digest(row["payload"]) != row["payload_digest"]:
        raise RequestRefused("diagnosis request integrity unavailable")
    return {
        "requestId": str(row["id"]),
        "runId": str(row["run_id"]),
        "requestedBy": str(row["requested_by"]),
        "scope": row["payload"],
        "scopeDigest": row["payload_digest"],
        "createdAt": to_rfc3339_utc(row["created_at"]),
        "expiresAt": to_rfc3339_utc(row["expires_at"]),
        "revokedAt": None if row["revoked_at"] is None else to_rfc3339_utc(row["revoked_at"]),
        "meaning": "HUMAN_REQUEST_NOT_MODEL_COMPLETION",
        "deliveryMode": "EXPLICIT_OPERATOR_DISPATCH",
    }


def _predecessor(
    conn: psycopg.Connection[Any],
    run_id: str,
    payload: dict[str, Any],
) -> None:
    sealed = conn.execute(
        "SELECT canonical_manifest FROM sealed_manifest WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if sealed is None or digest(sealed["canonical_manifest"]) != payload["manifestDigest"]:
        raise RequestRefused("original diagnosis manifest unavailable")
    group = diagnoses.group_digest(
        sealed["canonical_manifest"],
        payload["assertionId"],
        payload["componentPath"],
    )
    existing = conn.execute(
        "SELECT id FROM finding_diagnosis WHERE run_id=%s AND group_digest=%s LIMIT 1",
        (run_id, group),
    ).fetchone()
    if existing is None and payload["supersedes"] is None:
        return
    if (
        conn.execute(
            "SELECT 1 FROM finding_diagnosis d WHERE d.id=%s AND d.run_id=%s "
            "AND d.group_digest=%s AND d.deleted_at IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM finding_diagnosis child WHERE child.supersedes=d.id)",
            (payload["supersedes"], run_id, group),
        ).fetchone()
        is None
    ):
        raise RequestRefused(
            "follow-up requires the exact current predecessor before provider work"
        )


def create(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    requested_by: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    validate(payload)
    diagnoses.authorize(conn, workspace_id, requested_by)
    run = conn.execute("SELECT * FROM run WHERE id=%s FOR SHARE", (run_id,)).fetchone()
    evaluation = evaluations.read(conn, run_id=run_id)
    if (
        run is None
        or evaluation is None
        or run["status"] != "COMPLETED"
        or run["outcome"] not in {"FAIL", "INCONCLUSIVE"}
        or run["manifest_digest"] != payload["manifestDigest"]
        or evaluation["snapshotDigest"] != payload["evaluationDigest"]
        or evaluation["snapshot"]["outcome"] != run["outcome"]
        or payload["assertionId"]
        not in {item["assertionId"] for item in evaluation["snapshot"]["assertions"]}
    ):
        raise RequestRefused("exact completed diagnosis evaluation required")
    if conn.execute(
        "SELECT 1 FROM evidence_artifact WHERE attempt_id=%s AND retention='DELETED' LIMIT 1",
        (evaluation["snapshot"]["attemptId"],),
    ).fetchone():
        raise RequestRefused("diagnosis source evidence has been deleted")
    _predecessor(conn, run_id, payload)
    row = conn.execute(
        "INSERT INTO diagnosis_request(id,workspace_id,run_id,requested_by,payload,payload_digest) "
        "VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
        (str(uuid4()), workspace_id, run_id, requested_by, Jsonb(payload), digest(payload)),
    ).fetchone()
    assert row is not None
    return _view(row)


def inspect(conn: psycopg.Connection[Any], *, request_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM diagnosis_request WHERE id=%s", (request_id,)).fetchone()
    if row is None:
        raise LookupError("diagnosis request unavailable")
    result = _view(row)
    invocation = conn.execute(
        "SELECT status FROM diagnosis_invocation WHERE operation_id=%s", (request_id,)
    ).fetchone()
    result["invocationState"] = "NOT_STARTED" if invocation is None else invocation["status"]
    occurrence = diagnoses.by_operation(
        conn, workspace_id=str(row["workspace_id"]), operation_id=request_id
    )
    result["findingId"] = None if occurrence is None else occurrence["findingId"]
    return result


def require_active(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    request_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM diagnosis_request WHERE id=%s AND workspace_id=%s "
        "AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR SHARE",
        (request_id, workspace_id),
    ).fetchone()
    if row is None:
        raise RequestRefused("diagnosis request expired, revoked or unavailable")
    diagnoses.authorize(conn, workspace_id, str(row["requested_by"]))
    result = _view(row)
    validate(result["scope"])
    if diagnoses.by_operation(conn, workspace_id=workspace_id, operation_id=request_id) is None:
        _predecessor(conn, result["runId"], result["scope"])
    return result


def revoke(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    request_id: str,
    actor_id: str,
) -> dict[str, Any]:
    diagnoses.authorize(conn, workspace_id, actor_id)
    row = conn.execute(
        "SELECT * FROM diagnosis_request WHERE id=%s AND requested_by=%s FOR UPDATE",
        (request_id, actor_id),
    ).fetchone()
    if row is None:
        raise LookupError("request unavailable to this requester")
    if row["revoked_at"] is None:
        conn.execute(
            "UPDATE diagnosis_request SET revoked_at=clock_timestamp() WHERE id=%s", (request_id,)
        )
    return inspect(conn, request_id=request_id)
