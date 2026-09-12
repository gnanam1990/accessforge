"""Trusted protected-regression claims and receipts; no public attestation write interface."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg

from accessforge_domain.states import ApprovalScope, PatchStatus
from accessforge_domain.timestamps import to_rfc3339_utc

from . import approvals, patches, retention
from . import candidate_builds as builds

ROLES = ("database", "driver", "candidate", "restarted-candidate")
Refused = builds.BuildClaimRefused


@dataclass(frozen=True, slots=True)
class RegressionClaim:
    attempt_id: str
    build_id: str
    worker_token: str
    epoch: int


def _owned(
    conn: psycopg.Connection[dict[str, Any]], claim: RegressionClaim, state: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM candidate_regression_attempt WHERE id=%s FOR UPDATE", (claim.attempt_id,)
    ).fetchone()
    if (
        row is None
        or str(row["worker_token"]) != claim.worker_token
        or row["epoch"] != claim.epoch
        or row["state"] != state
        or str(row["build_id"]) != claim.build_id
        or row["lease_expires_at"] <= builds._moment(conn, None)
    ):
        raise Refused("regression attempt is expired, fenced, completed or foreign")
    return row


def _authority(
    conn: psycopg.Connection[dict[str, Any]], build_id: str, workspace_id: str
) -> dict[str, Any]:
    build = conn.execute(
        "SELECT * FROM candidate_build_attempt WHERE id=%s AND workspace_id=%s "
        "AND state='BUILT' FOR SHARE",
        (build_id, workspace_id),
    ).fetchone()
    if build is None:
        raise Refused("no completed candidate build is visible")
    conn.execute("SELECT id FROM patch_proposal WHERE id=%s FOR UPDATE", (build["patch_id"],))
    patch = patches.load_patch(conn, patch_id=str(build["patch_id"]))
    if (
        patch.status is not PatchStatus.BUILDING
        or patch.revision != build["building_revision"]
        or patch.patch_digest != build["patch_digest"]
        or patch.approval_id != str(build["approval_id"])
    ):
        raise Refused("candidate patch identity or revision changed")
    conn.execute("SELECT id FROM approval WHERE id=%s FOR SHARE", (build["approval_id"],))
    fields = {key: build[key] for key in builds.BuildInputs.__dataclass_fields__}
    fields["source_snapshot_id"] = str(build["source_snapshot_id"])
    inputs = builds.BuildInputs(**fields)
    builds._check_source(builds._baseline(conn, patch.patch_id, workspace_id), inputs)
    archive = conn.execute(
        "SELECT a.* FROM candidate_archive a WHERE a.build_id=%s AND a.state='RETAINED' "
        "AND a.content_digest=%s AND NOT EXISTS "
        "(SELECT 1 FROM candidate_archive_retirement r WHERE r.build_id=a.build_id) FOR SHARE OF a",
        (build_id, build["artifact_digest"]),
    ).fetchone()
    if archive is None:
        raise Refused("retained candidate is unavailable or retiring")
    sources = [
        entry
        for entry in retention.current_policy(conn, workspace_id=workspace_id).entries
        if entry.evidence_class == "SOURCE_SNAPSHOT"
    ]
    # Time-based authority is checked after all potentially blocking row/policy reads.
    moment = builds._moment(conn, None)
    approvals.load_for_check(conn, approval_id=str(build["approval_id"])).check(
        now=to_rfc3339_utc(moment),
        scope=ApprovalScope.PATCH_APPLY,
        workspace_id=workspace_id,
        target_id=patch.patch_id,
        target_digest=patch.patch_digest,
        current_revision=int(build["approved_revision"]),
    )
    if len(sources) != 1 or moment - archive["created_at"] >= timedelta(
        days=sources[0].retain_days
    ):
        raise Refused("candidate archive retention expired")
    return build


def claim(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    build_id: str,
    artifact_digest: str,
    policy_digest: str,
    image_id: str,
    daemon_endpoint: str,
    daemon_id: str,
) -> RegressionClaim:
    for value in (artifact_digest, policy_digest):
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise Refused("invalid regression digest")
    with conn.transaction():
        build = _authority(conn, build_id, workspace_id)
        process = conn.execute(
            "SELECT image_id FROM candidate_process_receipt WHERE build_id=%s", (build_id,)
        ).fetchone()
        if (
            build["artifact_digest"] != artifact_digest
            or process is None
            or process["image_id"] != image_id
            or build["daemon_endpoint"] != daemon_endpoint
            or build["daemon_id"] != daemon_id
        ):
            raise Refused("regression bytes, image or daemon differ from captured build")
        result = RegressionClaim(str(uuid.uuid4()), build_id, str(uuid.uuid4()), 1)
        inserted = conn.execute(
            "INSERT INTO candidate_regression_attempt "
            "(id,workspace_id,build_id,worker_token,artifact_digest,policy_digest,image_id,"
            "daemon_endpoint,daemon_id,state,lease_expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'CLAIMED',"
            "clock_timestamp()+interval '180 seconds') "
            "ON CONFLICT (build_id) DO NOTHING RETURNING id",
            (
                result.attempt_id,
                workspace_id,
                build_id,
                result.worker_token,
                artifact_digest,
                policy_digest,
                image_id,
                daemon_endpoint,
                daemon_id,
            ),
        ).fetchone()
        if inserted is None:
            raise Refused("regression attempt already exists; never automatically retry")
        return result


def dispatch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    policy_digest: str,
    artifact_digest: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "CLAIMED")
        _authority(conn, claim.build_id, str(row["workspace_id"]))
        if row["policy_digest"] != policy_digest or row["artifact_digest"] != artifact_digest:
            raise Refused("regression dispatch identity changed")
        _owned(conn, claim, "CLAIMED")
        conn.execute(
            "UPDATE candidate_regression_attempt SET state='DISPATCHED',"
            "dispatched_at=clock_timestamp() WHERE id=%s",
            (claim.attempt_id,),
        )


def planned(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    role: str,
    name: str,
    image_id: str,
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        _authority(conn, claim.build_id, str(row["workspace_id"]))
        _owned(conn, claim, "DISPATCHED")
        if role not in ROLES or name != f"accessforge-regression-{claim.attempt_id}-{role}":
            raise Refused("regression process name or role does not match durable intent")
        if role != "database" and image_id != row["image_id"]:
            raise Refused("candidate or driver image differs from the approved runtime")
        conn.execute(
            "INSERT INTO candidate_regression_process "
            "(attempt_id,workspace_id,role,container_name,image_id,state) "
            "VALUES (%s,%s,%s,%s,%s,'PLANNED')",
            (claim.attempt_id, row["workspace_id"], role, name, image_id),
        )


def created(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    role: str,
    container_id: str,
    image_id: str,
) -> None:
    with conn.transaction():
        # An observed creation fact survives a fence. Recording it does NOT authorize start;
        # the coordinator commits the fact then separately rechecks live authority.
        owner = conn.execute(
            "SELECT worker_token,build_id FROM candidate_regression_attempt WHERE id=%s FOR UPDATE",
            (claim.attempt_id,),
        ).fetchone()
        if (
            owner is None
            or str(owner["worker_token"]) != claim.worker_token
            or str(owner["build_id"]) != claim.build_id
        ):
            raise Refused("foreign regression process receipt")
        changed = conn.execute(
            "UPDATE candidate_regression_process SET state='CREATED',container_id=%s "
            "WHERE attempt_id=%s AND role=%s AND state='PLANNED' AND image_id=%s",
            (container_id, claim.attempt_id, role, image_id),
        ).rowcount
        if changed != 1:
            raise Refused("process receipt does not match its immutable plan")


def assert_active(conn: psycopg.Connection[dict[str, Any]], *, claim: RegressionClaim) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        _authority(conn, claim.build_id, str(row["workspace_id"]))
        _owned(conn, claim, "DISPATCHED")


def removed(conn: psycopg.Connection[dict[str, Any]], *, claim: RegressionClaim, role: str) -> None:
    # Removal facts can arrive after fencing, but never renew a lease or turn UNKNOWN into PASS.
    with conn.transaction():
        row = conn.execute(
            "SELECT worker_token FROM candidate_regression_attempt WHERE id=%s", (claim.attempt_id,)
        ).fetchone()
        if row is None or str(row["worker_token"]) != claim.worker_token:
            raise Refused("foreign regression cleanup receipt")
        changed = conn.execute(
            "UPDATE candidate_regression_process SET state='REMOVED',"
            "removed_at=clock_timestamp() WHERE attempt_id=%s AND role=%s "
            "AND state='CREATED'",
            (claim.attempt_id, role),
        ).rowcount
        if changed != 1:
            raise Refused("cleanup has no matching created process")


def finish(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    policy_digest: str,
    artifact_digest: str,
    checks: tuple[str, ...],
    containers: tuple[tuple[str, str, str], ...],
) -> None:
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        _authority(conn, claim.build_id, str(row["workspace_id"]))
        if row["policy_digest"] != policy_digest:
            raise Refused("regression policy changed before receipt")
        if row["artifact_digest"] != artifact_digest:
            raise Refused("regression artifact changed before receipt")
        if (
            not checks
            or len(checks) > 128
            or len(set(checks)) != len(checks)
            or any(not re.fullmatch(r"[a-z0-9_]{1,100}", value) for value in checks)
        ):
            raise Refused("regression result identity or check set is invalid")
        processes = conn.execute(
            "SELECT role,container_id,image_id,state FROM candidate_regression_process "
            "WHERE attempt_id=%s",
            (claim.attempt_id,),
        ).fetchall()
        actual = {(str(p["role"]), str(p["container_id"]), str(p["image_id"])) for p in processes}
        if (
            {p["role"] for p in processes} != set(ROLES)
            or actual != set(containers)
            or len(containers) != len(ROLES)
            or any(p["state"] != "REMOVED" for p in processes)
        ):
            raise Refused("regression result lacks exact process/cleanup receipts")
        _owned(conn, claim, "DISPATCHED")
        conn.execute(
            "UPDATE candidate_regression_attempt SET state='PASSED',cleanup_confirmed=true,"
            "checks=%s,finished_at=clock_timestamp() WHERE id=%s",
            (list(checks), claim.attempt_id),
        )


def fail(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    cleanup_confirmed: bool,
    failure_code: str = "REGRESSION_FAILED",
) -> None:
    if failure_code not in {"REGRESSION_FAILED", "CANCELLED_CONFIRMED", "EXECUTION_INTERRUPTED"}:
        raise Refused("invalid regression failure classification")
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED")
        unresolved = conn.execute(
            "SELECT 1 FROM candidate_regression_process WHERE attempt_id=%s "
            "AND state<>'REMOVED' LIMIT 1",
            (claim.attempt_id,),
        ).fetchone()
        clean = cleanup_confirmed and unresolved is None
        conn.execute(
            "UPDATE candidate_regression_attempt SET state=%s,epoch=epoch+1,"
            "cleanup_confirmed=%s,failure_code=%s,finished_at=clock_timestamp() WHERE id=%s",
            (
                "FAILED" if clean else "UNKNOWN",
                clean,
                failure_code if clean else "CLEANUP_UNCONFIRMED",
                row["id"],
            ),
        )


def fence_expired(conn: psycopg.Connection[dict[str, Any]], *, now: datetime | None = None) -> int:
    return conn.execute(
        "UPDATE candidate_regression_attempt SET state='UNKNOWN',epoch=epoch+1,"
        "failure_code='LEASE_EXPIRED',finished_at=clock_timestamp() "
        "WHERE state IN ('CLAIMED','DISPATCHED') AND lease_expires_at<=%s",
        (builds._moment(conn, now),),
    ).rowcount
