"""Historical protected validation receipt; never live authority or a reader/run verdict."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc

from . import candidate_fixture_setups, candidate_regressions

Refused = candidate_regressions.Refused
VALIDATION_CHECKS = frozenset(
    f"{prefix}_{field}"
    for prefix in ("reject_invalid", "no_invalid_write")
    for field in ("email", "full_name", "category", "description")
)


def producer(attempt_id: str) -> str:
    return "functional-regression:" + attempt_id


def snapshot(conn: psycopg.Connection[dict[str, Any]], session: dict[str, Any]) -> dict[str, Any]:
    receipt = for_run(conn, run_id=str(session["run_id"]))
    if (
        receipt is None
        or receipt["workspaceId"] != str(session["workspace_id"])
        or receipt["leaseId"] != str(session["lease_id"])
        or receipt["leaseEpoch"] != session["epoch"]
    ):
        raise Refused("original completed functional receipt for this execution is unavailable")
    return {
        "format": "accessforge.functional-regression-artifact.v1",
        "runId": str(session["run_id"]),
        "attemptId": str(session["attempt_id"]),
        "manifestDigest": session["manifest_digest"],
        "producerId": producer(str(session["attempt_id"])),
        "receipt": receipt,
        "receiptDigest": digest(receipt),
    }


def for_run(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> dict[str, Any] | None:
    """Read an original completed receipt, including after endpoint/reader cleanup.

    None means no completed supported evidence. Contradictory original bindings refuse.
    Consumers must still retain these bytes and bind the observed runtime build before deriving
    frozen assertions. A result here does not open the patch verification gate.
    """
    row = conn.execute(
        "SELECT a.*,b.endpoint_binding_digest,b.fixture_template_digest,"
        "c.lease_id,c.lease_epoch,l.epoch AS actual_epoch,l.released_at,"
        "l.run_id AS leased_run,e.state AS endpoint_state,e.cleanup_confirmed AS endpoint_clean,"
        "e.binding_digest,e.receipt AS endpoint_receipt "
        "FROM candidate_run_binding b JOIN candidate_regression_attempt a "
        "ON a.id=b.regression_attempt_id AND a.workspace_id=b.workspace_id "
        "AND a.build_id=b.build_id JOIN candidate_reader_lease c "
        "ON c.run_id=b.run_id AND c.workspace_id=b.workspace_id "
        "JOIN desktop_lease l ON l.id=c.lease_id AND l.workspace_id=c.workspace_id "
        "JOIN candidate_endpoint e ON e.attempt_id=a.id AND e.workspace_id=a.workspace_id "
        "WHERE b.run_id=%s",
        (run_id,),
    ).fetchone()
    if row is None or row["state"] != "PASSED":
        return None
    checks = row["checks"]
    if not VALIDATION_CHECKS.issubset(checks):
        return None  # PASSED for a different/partial check set cannot answer validation assertions.
    if (
        not row["cleanup_confirmed"]
        or not row["endpoint_clean"]
        or row["endpoint_state"] != "CLOSED"
        or row["endpoint_receipt"] is None
        or row["binding_digest"] != row["endpoint_binding_digest"]
        or str(row["leased_run"]) != run_id
        or row["lease_epoch"] != row["actual_epoch"]
        or row["released_at"] is None
        or row["finished_at"] is None
        or row["released_at"] > row["finished_at"]
    ):
        raise Refused("functional receipt lacks original endpoint/reader cleanup binding")
    seed = candidate_fixture_setups.for_run(conn, run_id=run_id)
    if seed is None:
        raise Refused("functional receipt lacks original fixture/build lineage")
    processes = conn.execute(
        "SELECT role,container_id,image_id,state FROM candidate_regression_process "
        "WHERE attempt_id=%s AND workspace_id=%s ORDER BY role",
        (row["id"], row["workspace_id"]),
    ).fetchall()
    if (
        len(processes) != len(candidate_regressions.ROLES)
        or len({p["container_id"] for p in processes}) != len(candidate_regressions.ROLES)
        or {p["role"] for p in processes} != set(candidate_regressions.ROLES)
        or any(p["state"] != "REMOVED" or not p["container_id"] for p in processes)
        or any(p["role"] != "database" and p["image_id"] != row["image_id"] for p in processes)
        or any(
            p["role"] in seed["context"]["processes"]
            and p["container_id"] != seed["context"]["processes"][p["role"]]
            for p in processes
        )
    ):
        raise Refused("functional receipt process identities or cleanup differ")
    return {
        "format": "accessforge.functional-regression.v1",
        "meaning": "PROTECTED_VALIDATION_CHECKS_NOT_READER_OR_RUN_VERDICT",
        "workspaceId": str(row["workspace_id"]),
        "runId": run_id,
        "regressionAttemptId": str(row["id"]),
        "buildId": str(row["build_id"]),
        "artifactDigest": row["artifact_digest"],
        "policyDigest": row["policy_digest"],
        "fixtureTemplateDigest": row["fixture_template_digest"],
        "leaseId": str(row["lease_id"]),
        "leaseEpoch": row["lease_epoch"],
        "endpointBindingDigest": row["endpoint_binding_digest"],
        "originalSeedDigest": digest(seed),
        "checks": sorted(checks),
        "processes": [dict(p) for p in processes],
        "finishedAt": to_rfc3339_utc(row["finished_at"]),
        **(
            {"producerReceipt": row["functional_receipt"]}
            if row["functional_receipt"] is not None
            else {}
        ),
    }
