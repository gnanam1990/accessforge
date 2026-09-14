"""Owned original-build functional execution; no reader verdict or public attestation writes."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.functional_validation import ValidationObservation

from . import baseline_builds as builds
from . import retention
from .candidate_regressions import ROLES, RegressionClaim

Refused = builds.Refused


def _authority(conn: psycopg.Connection[Any], build_id: str) -> dict[str, Any]:
    build = conn.execute("SELECT * FROM baseline_build_attempt WHERE id=%s", (build_id,)).fetchone()
    if build is None or build["state"] != "CAPTURED":
        raise Refused("original baseline capture unavailable")
    if (
        builds.read_binding(
            conn, workspace_id=str(build["workspace_id"]), run_id=str(build["run_id"])
        )
        != build["binding"]
    ):
        raise Refused("original baseline authority changed")
    conn.execute("SELECT id FROM baseline_build_attempt WHERE id=%s FOR UPDATE", (build_id,))
    archive = conn.execute(
        "SELECT * FROM baseline_archive WHERE build_id=%s AND state='RETAINED' "
        "AND NOT EXISTS(SELECT 1 FROM baseline_archive_retirement WHERE build_id=%s) FOR SHARE",
        (build_id, build_id),
    ).fetchone()
    policy = [
        e
        for e in retention.current_policy(conn, workspace_id=str(build["workspace_id"])).entries
        if e.evidence_class == "SOURCE_SNAPSHOT"
    ]
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()
    assert now is not None
    if (
        archive is None
        or archive["content_digest"] != build["artifact_digest"]
        or len(policy) != 1
        or now["now"] - archive["created_at"] >= timedelta(days=policy[0].retain_days)
    ):
        raise Refused("original baseline archive unavailable or expired")
    # Policy/archive locks may have blocked past approval expiry; recheck at the final boundary.
    builds.read_binding(conn, workspace_id=str(build["workspace_id"]), run_id=str(build["run_id"]))
    return dict(build)


def _owned(
    conn: psycopg.Connection[Any], claim: RegressionClaim, state: str, *, authorize: bool = True
) -> dict[str, Any]:
    # Global order: original run, build, then attempt. Cleanup facts do not renew authority.
    identity = conn.execute(
        "SELECT run_id FROM baseline_regression_attempt WHERE id=%s", (claim.attempt_id,)
    ).fetchone()
    if identity is None:
        raise Refused("baseline runtime attempt unavailable")
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (identity["run_id"],))
    if authorize:
        _authority(conn, claim.build_id)
    row = conn.execute(
        "SELECT * FROM baseline_regression_attempt WHERE id=%s FOR UPDATE", (claim.attempt_id,)
    ).fetchone()
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()
    assert now is not None
    if (
        row is None
        or str(row["build_id"]) != claim.build_id
        or str(row["worker_token"]) != claim.worker_token
        or row["epoch"] != claim.epoch
        or row["state"] != state
        or row["lease_expires_at"] <= now["now"]
    ):
        raise Refused("baseline runtime is foreign, expired, fenced or terminal")
    return dict(row)


def claim(
    conn: psycopg.Connection[Any],
    *,
    build_id: str,
    artifact_digest: str,
    policy_digest: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> RegressionClaim:
    if any(not re.fullmatch(r"[a-f0-9]{64}", d) for d in (artifact_digest, policy_digest)):
        raise Refused("invalid baseline runtime digest")
    with conn.transaction():
        build = _authority(conn, build_id)
        if (
            build["artifact_digest"],
            build["image_id"],
            build["daemon_endpoint"],
            build["daemon_id"],
        ) != (artifact_digest, image_id, daemon_endpoint, daemon_id):
            raise Refused("baseline runtime differs from captured build")
        value = RegressionClaim(str(uuid.uuid4()), build_id, str(uuid.uuid4()), 1)
        row = conn.execute(
            "INSERT INTO baseline_regression_attempt(id,workspace_id,build_id,run_id,worker_token,"
            "artifact_digest,policy_digest,image_id,daemon_endpoint,daemon_id,"
            "state,lease_expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CLAIMED',"
            "clock_timestamp()+interval '180 seconds') "
            "ON CONFLICT(build_id) DO NOTHING RETURNING id",
            (
                value.attempt_id,
                build["workspace_id"],
                build_id,
                build["run_id"],
                value.worker_token,
                artifact_digest,
                policy_digest,
                image_id,
                daemon_endpoint,
                daemon_id,
            ),
        ).fetchone()
        if row is None:
            raise Refused("baseline runtime already attempted; no implicit retry")
        return value


def dispatch(
    conn: psycopg.Connection[Any],
    *,
    claim: RegressionClaim,
    policy_digest: str,
    artifact_digest: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "CLAIMED")
        if (row["policy_digest"], row["artifact_digest"]) != (policy_digest, artifact_digest):
            raise Refused("baseline runtime dispatch changed")
        conn.execute(
            "UPDATE baseline_regression_attempt SET state='DISPATCHED',"
            "dispatched_at=clock_timestamp() WHERE id=%s",
            (claim.attempt_id,),
        )


def planned(
    conn: psycopg.Connection[Any], *, claim: RegressionClaim, role: str, name: str, image_id: str
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        if role not in ROLES or name != f"accessforge-regression-{claim.attempt_id}-{role}":
            raise Refused("baseline process plan identity changed")
        if role != "database" and image_id != row["image_id"]:
            raise Refused("baseline runtime toolchain changed")
        conn.execute(
            "INSERT INTO baseline_regression_process"
            "(attempt_id,workspace_id,role,container_name,image_id,state) "
            "VALUES(%s,%s,%s,%s,%s,'PLANNED')",
            (claim.attempt_id, row["workspace_id"], role, name, image_id),
        )


def _fact_owner(conn: psycopg.Connection[Any], claim: RegressionClaim) -> None:
    row = conn.execute(
        "SELECT worker_token,build_id FROM baseline_regression_attempt WHERE id=%s FOR UPDATE",
        (claim.attempt_id,),
    ).fetchone()
    if (
        row is None
        or str(row["worker_token"]) != claim.worker_token
        or str(row["build_id"]) != claim.build_id
    ):
        raise Refused("foreign baseline process fact")


def created(
    conn: psycopg.Connection[Any],
    *,
    claim: RegressionClaim,
    role: str,
    container_id: str,
    image_id: str,
) -> None:
    with conn.transaction():
        _fact_owner(conn, claim)
        if (
            conn.execute(
                "UPDATE baseline_regression_process SET state='CREATED',container_id=%s "
                "WHERE attempt_id=%s AND role=%s AND image_id=%s AND state='PLANNED'",
                (container_id, claim.attempt_id, role, image_id),
            ).rowcount
            != 1
        ):
            raise Refused("baseline process creation differs from its plan")


def assert_active(conn: psycopg.Connection[Any], *, claim: RegressionClaim) -> None:
    with conn.transaction():
        _owned(conn, claim, "DISPATCHED")


def removed(conn: psycopg.Connection[Any], *, claim: RegressionClaim, role: str) -> None:
    with conn.transaction():
        _fact_owner(conn, claim)
        if (
            conn.execute(
                "UPDATE baseline_regression_process SET state='REMOVED',"
                "removed_at=clock_timestamp() WHERE attempt_id=%s AND role=%s "
                "AND state='CREATED'",
                (claim.attempt_id, role),
            ).rowcount
            != 1
        ):
            raise Refused("baseline cleanup has no original creation receipt")


def finish(
    conn: psycopg.Connection[Any],
    *,
    claim: RegressionClaim,
    policy_digest: str,
    artifact_digest: str,
    checks: tuple[str, ...],
    containers: tuple[tuple[str, str, str], ...],
    validation: ValidationObservation,
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        if (row["policy_digest"], row["artifact_digest"]) != (policy_digest, artifact_digest):
            raise Refused("baseline runtime result identity changed")
        if (
            not checks
            or len(checks) > 128
            or len(set(checks)) != len(checks)
            or any(not re.fullmatch(r"[a-z0-9_]{1,100}", c) for c in checks)
            or type(validation) is not ValidationObservation
            or not validation.passed
        ):
            raise Refused("baseline runtime lacks complete passing validation measurements")
        rows = conn.execute(
            "SELECT * FROM baseline_regression_process WHERE attempt_id=%s", (claim.attempt_id,)
        ).fetchall()
        if (
            {p["role"] for p in rows} != set(ROLES)
            or len(containers) != len(ROLES)
            or {(p["role"], p["container_id"], p["image_id"]) for p in rows} != set(containers)
            or any(p["state"] != "REMOVED" for p in rows)
        ):
            raise Refused("baseline runtime lacks exact process cleanup")
        _owned(conn, claim, "DISPATCHED")
        conn.execute(
            "UPDATE baseline_regression_attempt SET state='PASSED',cleanup_confirmed=true,"
            "checks=%s,validation=%s,finished_at=clock_timestamp() WHERE id=%s",
            (list(checks), Jsonb(validation.canonical_form()), claim.attempt_id),
        )


def fail(conn: psycopg.Connection[Any], *, claim: RegressionClaim, cleanup_confirmed: bool) -> None:
    with conn.transaction():
        _owned(conn, claim, "DISPATCHED", authorize=False)
        unresolved = conn.execute(
            "SELECT 1 FROM baseline_regression_process WHERE attempt_id=%s AND state<>'REMOVED'",
            (claim.attempt_id,),
        ).fetchone()
        clean = cleanup_confirmed and unresolved is None
        conn.execute(
            "UPDATE baseline_regression_attempt SET state=%s,epoch=epoch+1,"
            "cleanup_confirmed=%s,failure_code=%s,finished_at=clock_timestamp() WHERE id=%s",
            (
                "FAILED" if clean else "UNKNOWN",
                clean,
                "REGRESSION_FAILED" if clean else "CLEANUP_UNCONFIRMED",
                claim.attempt_id,
            ),
        )


def fence_expired(conn: psycopg.Connection[Any], *, now: datetime | None = None) -> int:
    return conn.execute(
        "UPDATE baseline_regression_attempt SET state='UNKNOWN',epoch=epoch+1,"
        "failure_code='LEASE_EXPIRED',finished_at=clock_timestamp() "
        "WHERE state IN ('CLAIMED','DISPATCHED') "
        "AND lease_expires_at<=coalesce(%s,clock_timestamp())",
        (now,),
    ).rowcount
