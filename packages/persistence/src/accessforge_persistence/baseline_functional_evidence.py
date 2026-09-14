"""Historical protected validation receipt; never live authority or a reader/run verdict."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc

from . import baseline_fixture_evidence, baseline_runs, candidate_regressions

Refused = candidate_regressions.Refused
VALIDATION_CHECKS = frozenset(
    f"{prefix}_{field}"
    for prefix in ("reject_invalid", "no_invalid_write")
    for field in ("email", "full_name", "category", "description")
)


def for_run(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> dict[str, Any] | None:
    """Read an original completed receipt, including after endpoint/reader cleanup.

    None means no completed supported evidence. Contradictory original bindings refuse.
    Consumers must still retain these bytes and bind the observed runtime build before deriving
    frozen assertions. A result here does not open the patch verification gate.
    """
    row = conn.execute(
        "SELECT a.*,b.endpoint_binding_digest,b.manifest_digest AS session_manifest_digest,"
        "i.template_digest AS fixture_template_digest,i.nonce AS fixture_nonce,"
        "c.lease_id,c.lease_epoch,l.epoch AS actual_epoch,l.released_at,"
        "l.run_id AS leased_run,e.state AS endpoint_state,e.cleanup_confirmed AS endpoint_clean,"
        "e.binding_digest,e.receipt AS endpoint_receipt "
        "FROM baseline_session_binding b JOIN baseline_regression_attempt a "
        "ON a.id=b.regression_attempt_id AND a.workspace_id=b.workspace_id "
        "AND a.run_id=b.run_id JOIN run_fixture_instance i ON i.run_id=b.run_id "
        "AND i.workspace_id=b.workspace_id JOIN baseline_reader_lease c "
        "ON c.run_id=b.run_id AND c.workspace_id=b.workspace_id "
        "JOIN desktop_lease l ON l.id=c.lease_id AND l.workspace_id=c.workspace_id "
        "JOIN baseline_endpoint e ON e.attempt_id=a.id AND e.workspace_id=a.workspace_id "
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
    if not baseline_runs.reader_cleanup_confirmed(conn, attempt_id=str(row["id"])):
        raise Refused("baseline functional receipt has no acknowledged original reader stop")
    seed = conn.execute(
        "SELECT context,context_digest,observation,observation_digest "
        "FROM fixture_setup_reservation "
        "WHERE run_id=%s AND workspace_id=%s",
        (run_id, row["workspace_id"]),
    ).fetchone()
    if (
        seed is None
        or not isinstance(seed["observation"], dict)
        or digest(seed["context"]) != seed["context_digest"]
        or digest(seed["observation"]) != seed["observation_digest"]
        or seed["observation"].get("contextDigest") != seed["context_digest"]
        or seed["context"].get("runId") != run_id
        or seed["context"].get("workspaceId") != str(row["workspace_id"])
        or seed["context"].get("templateDigest") != row["fixture_template_digest"]
        or seed["context"].get("manifestDigest") != row["session_manifest_digest"]
        or seed["context"].get("nonce") != row["fixture_nonce"]
        or "baselineRuntime" not in seed["observation"]
    ):
        raise Refused("baseline functional receipt lacks original fixture/build lineage")
    try:
        baseline_fixture_evidence.validate_runtime(
            conn,
            run_id=run_id,
            workspace_id=str(row["workspace_id"]),
            observation=seed["observation"],
        )
    except ValueError as exc:
        raise Refused("baseline functional seed provenance differs") from exc
    seed_processes = seed["observation"]["baselineRuntime"]["processes"]
    processes = conn.execute(
        "SELECT role,container_id,image_id,state FROM baseline_regression_process "
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
            p["role"] in seed_processes
            and p["container_id"] != seed_processes[p["role"]]["containerId"]
            for p in processes
        )
    ):
        raise Refused("functional receipt process identities or cleanup differ")
    return {
        "format": "accessforge.functional-regression.v1",
        "runtimeKind": "BASELINE",
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
