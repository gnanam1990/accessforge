"""Durable build claims with exact approval revision binding and fail-closed lease fencing.

Only the trusted build coordinator calls these functions; there is no author-facing write API.
Input archive identities come from the source broker/preparer, never build stdout. This store
attests scheduling/receipt state, not protected regressions or actual reader outcomes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.states import ApprovalScope, PatchStatus
from accessforge_domain.timestamps import to_rfc3339_utc

from . import approvals, patches


class BuildClaimRefused(RuntimeError):
    """The attempt cannot be dispatched or completed by this worker."""


@dataclass(frozen=True, slots=True)
class BuildInputs:
    source_snapshot_id: str
    source_commit: str
    source_tree_digest: str
    base_archive_digest: str
    candidate_archive_digest: str
    policy_digest: str
    surface_digest: str
    patch_digest: str
    approved_revision: int

    def __post_init__(self) -> None:
        uuid.UUID(self.source_snapshot_id)
        if self.approved_revision < 1:
            raise BuildClaimRefused("invalid approved revision")
        if not re.fullmatch(r"[a-f0-9]{40}", self.source_commit):
            raise BuildClaimRefused("invalid source commit")
        for value in (
            self.source_tree_digest,
            self.base_archive_digest,
            self.candidate_archive_digest,
            self.policy_digest,
            self.surface_digest,
            self.patch_digest,
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise BuildClaimRefused("invalid build input digest")


@dataclass(frozen=True, slots=True)
class BuildClaim:
    build_id: str
    patch_id: str
    verification_id: str
    worker_token: str
    epoch: int
    state: str


def _claim(row: dict[str, Any]) -> BuildClaim:
    return BuildClaim(
        str(row["id"]),
        str(row["patch_id"]),
        str(row["verification_id"]),
        str(row["worker_token"]),
        int(row["epoch"]),
        str(row["state"]),
    )


def _moment(conn: psycopg.Connection[dict[str, Any]], now: datetime | None) -> datetime:
    if now is not None:
        return now
    row = conn.execute("SELECT clock_timestamp() AS moment").fetchone()
    assert row is not None
    value: datetime = row["moment"]
    return value


def _baseline(
    conn: psycopg.Connection[dict[str, Any]],
    patch_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT m.*, s.commit_sha, s.tree_digest, s.dirty, s.dirty_path_count,
               f.run_id AS baseline_run_id, surface.paths, surface.revision AS surface_revision
          FROM patch_proposal pp
          JOIN finding f ON f.id = pp.finding_id AND f.workspace_id = pp.workspace_id
          JOIN run r ON r.id = f.run_id AND r.workspace_id = f.workspace_id
          JOIN sealed_manifest m ON m.manifest_digest = pp.base_manifest_digest
               AND m.manifest_digest = r.manifest_digest AND m.workspace_id = pp.workspace_id
               AND m.project_id = r.project_id
          JOIN source_snapshot s ON s.id = m.source_snapshot_id
               AND s.workspace_id = m.workspace_id AND s.project_id = m.project_id
          JOIN project p ON p.id = m.project_id AND p.workspace_id = m.workspace_id
          JOIN project_repair_surface surface ON surface.project_id = p.id
               AND surface.workspace_id = p.workspace_id
         WHERE pp.id = %s AND pp.workspace_id = %s
               AND p.revoked_at IS NULL AND p.repository_authorized_by IS NOT NULL
         LIMIT 1 FOR SHARE OF s, p, surface
        """,
        (patch_id, workspace_id),
    ).fetchone()
    if row is None:
        raise BuildClaimRefused("no authorized exact baseline/source/repair surface")
    return row


def surface_identity(paths: tuple[str, ...], revision: int) -> str:
    return digest({"paths": list(paths), "revision": revision})


def _check_source(row: dict[str, Any], inputs: BuildInputs) -> None:
    if (
        str(row["source_snapshot_id"]) != inputs.source_snapshot_id
        or row["commit_sha"] != inputs.source_commit
        or row["tree_digest"] != inputs.source_tree_digest
        or row["dirty"]
        or row["dirty_path_count"]
        or surface_identity(tuple(row["paths"]), int(row["surface_revision"]))
        != inputs.surface_digest
    ):
        raise BuildClaimRefused("source or repair-surface identity changed")


def claim_build(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    patch_id: str,
    inputs: BuildInputs,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> BuildClaim:
    """Atomically open verification and claim one attempt, with a durable task ID.

    Caller must commit this transaction before invoking Docker. No automatic claim replacement;
    even lease expiry cannot establish that the first attempt did not execute.
    """
    if not 1 <= lease_seconds <= 900:
        raise BuildClaimRefused("invalid build lease")
    moment = now or datetime.now(UTC)
    with conn.transaction():
        conn.execute("SELECT id FROM patch_proposal WHERE id = %s FOR UPDATE", (patch_id,))
        patch = patches.assert_dispatchable(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            current_source_digest=inputs.source_tree_digest,
            now=moment,
        )
        assert patch.approval_id is not None
        if patch.patch_digest != inputs.patch_digest or patch.revision != inputs.approved_revision:
            raise BuildClaimRefused("prepared candidate belongs to another approved patch revision")
        conn.execute("SELECT id FROM approval WHERE id = %s FOR SHARE", (patch.approval_id,))
        # Re-read after the lock: a revocation may have committed while acquiring it.
        patches.assert_dispatchable(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            current_source_digest=inputs.source_tree_digest,
            now=moment,
        )
        baseline = _baseline(conn, patch_id, workspace_id)
        _check_source(baseline, inputs)
        moment = _moment(conn, now)
        verification = patches.open_verification(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            baseline_run_id=str(baseline["baseline_run_id"]),
            baseline_identity={key: baseline[key] for key in patches.COMPARED_IDENTITY},
            now=moment,
        )
        row = conn.execute(
            """
            INSERT INTO candidate_build_attempt (
                id, workspace_id, patch_id, verification_id, project_id, source_snapshot_id,
                approval_id, approved_revision, building_revision, source_commit,
                source_tree_digest,
                base_archive_digest, candidate_archive_digest, patch_digest, policy_digest,
                surface_digest, worker_token, state, lease_expires_at, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CLAIMED',%s,%s)
            RETURNING *
            """,
            (
                str(uuid.uuid4()),
                workspace_id,
                patch_id,
                verification.verification_id,
                baseline["project_id"],
                inputs.source_snapshot_id,
                patch.approval_id,
                patch.revision,
                patch.revision + 1,
                inputs.source_commit,
                inputs.source_tree_digest,
                inputs.base_archive_digest,
                inputs.candidate_archive_digest,
                patch.patch_digest,
                inputs.policy_digest,
                inputs.surface_digest,
                str(uuid.uuid4()),
                moment + timedelta(seconds=lease_seconds),
                moment,
            ),
        ).fetchone()
        assert row is not None
        return _claim(row)


def _owned(
    conn: psycopg.Connection[dict[str, Any]],
    claim: BuildClaim,
    state: str,
    moment: datetime | None,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM candidate_build_attempt WHERE id = %s FOR UPDATE", (claim.build_id,)
    ).fetchone()
    moment = _moment(conn, moment)
    if (
        row is None
        or str(row["worker_token"]) != claim.worker_token
        or row["epoch"] != claim.epoch
        or row["state"] != state
        or row["lease_expires_at"] <= moment
    ):
        raise BuildClaimRefused("attempt is expired, fenced, completed or owned by another worker")
    return row


def authorize_dispatch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    claim: BuildClaim,
    inputs: BuildInputs,
    now: datetime | None = None,
) -> BuildClaim:
    """Consume CLAIMED once, after a fresh authority check; commit before container creation."""
    moment = now or datetime.now(UTC)
    with conn.transaction():
        row = _owned(conn, claim, "CLAIMED", now)
        if str(row["workspace_id"]) != workspace_id:
            raise BuildClaimRefused("workspace mismatch")
        for key in BuildInputs.__dataclass_fields__:
            if str(row[key]) != str(getattr(inputs, key)):
                raise BuildClaimRefused("dispatch inputs differ from the durable claim")
        conn.execute("SELECT id FROM patch_proposal WHERE id = %s FOR UPDATE", (row["patch_id"],))
        patch = patches.load_patch(conn, patch_id=str(row["patch_id"]))
        if (
            patch.status is not PatchStatus.BUILDING
            or patch.revision != row["building_revision"]
            or patch.patch_digest != row["patch_digest"]
            or patch.base_source_digest != row["source_tree_digest"]
            or patch.approval_id != str(row["approval_id"])
        ):
            raise BuildClaimRefused("claimed patch revision or identity changed")
        conn.execute("SELECT id FROM approval WHERE id = %s FOR SHARE", (row["approval_id"],))
        _check_source(_baseline(conn, patch.patch_id, workspace_id), inputs)
        moment = _moment(conn, now)
        if row["lease_expires_at"] <= moment:
            raise BuildClaimRefused("attempt expired while waiting for dispatch locks")
        approvals.load_for_check(conn, approval_id=str(row["approval_id"])).check(
            now=to_rfc3339_utc(moment),
            scope=ApprovalScope.PATCH_APPLY,
            workspace_id=workspace_id,
            target_id=patch.patch_id,
            target_digest=patch.patch_digest,
            current_revision=int(row["approved_revision"]),
        )
        updated = conn.execute(
            "UPDATE candidate_build_attempt SET state = 'DISPATCHED', dispatched_at = %s "
            "WHERE id = %s RETURNING *",
            (moment, claim.build_id),
        ).fetchone()
        assert updated is not None
        return _claim(updated)


def finish_build(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: BuildClaim,
    artifact_digest: str,
    candidate_archive_digest: str,
    cleanup_confirmed: bool,
    now: datetime | None = None,
) -> BuildClaim:
    """Persist a trusted supervisor receipt, never a build's stdout or verification conclusion."""
    if not re.fullmatch(r"[a-f0-9]{64}", artifact_digest) or cleanup_confirmed is not True:
        raise BuildClaimRefused("built receipt requires a captured digest and confirmed cleanup")
    moment = now or datetime.now(UTC)
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED", now)
        conn.execute("SELECT id FROM patch_proposal WHERE id = %s FOR UPDATE", (row["patch_id"],))
        patch = patches.load_patch(conn, patch_id=str(row["patch_id"]))
        if patch.status is not PatchStatus.BUILDING or patch.revision != row["building_revision"]:
            raise BuildClaimRefused("patch changed after dispatch; receipt cannot be published")
        moment = _moment(conn, now)
        if row["lease_expires_at"] <= moment:
            raise BuildClaimRefused("attempt expired while waiting for receipt locks")
        if row["candidate_archive_digest"] != candidate_archive_digest:
            raise BuildClaimRefused("receipt is for a different candidate")
        updated = conn.execute(
            "UPDATE candidate_build_attempt SET state = 'BUILT', artifact_digest = %s, "
            "cleanup_confirmed = true, finished_at = %s WHERE id = %s RETURNING *",
            (artifact_digest, moment, claim.build_id),
        ).fetchone()
        assert updated is not None
        return _claim(updated)


def fence_expired(conn: psycopg.Connection[dict[str, Any]], *, now: datetime | None = None) -> int:
    """Expiry is UNKNOWN, not permission to run again. RLS confines this sweep to one workspace."""
    moment = _moment(conn, now)
    return conn.execute(
        "UPDATE candidate_build_attempt SET state = 'UNKNOWN', epoch = epoch + 1, "
        "finished_at = %s, failure_code = 'LEASE_EXPIRED' "
        "WHERE state IN ('CLAIMED','DISPATCHED') AND lease_expires_at <= %s",
        (moment, moment),
    ).rowcount


def record_failure(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: BuildClaim,
    cleanup_confirmed: bool,
    now: datetime | None = None,
) -> None:
    """Record supervisor-observed failure. Never save source stderr as an operational error."""
    with conn.transaction():
        row = _owned(conn, claim, "DISPATCHED", now)
        conn.execute("SELECT id FROM patch_proposal WHERE id = %s FOR UPDATE", (row["patch_id"],))
        moment = _moment(conn, now)
        if row["lease_expires_at"] <= moment:
            raise BuildClaimRefused("attempt expired while waiting for failure locks")
        state = "FAILED" if cleanup_confirmed else "UNKNOWN"
        conn.execute(
            "UPDATE candidate_build_attempt SET state = %s, epoch = epoch + 1, finished_at = %s, "
            "cleanup_confirmed = %s, failure_code = %s WHERE id = %s",
            (
                state,
                moment,
                cleanup_confirmed,
                "BUILD_FAILED" if cleanup_confirmed else "CLEANUP_UNCONFIRMED",
                claim.build_id,
            ),
        )
        if cleanup_confirmed:
            patch = patches.load_patch(conn, patch_id=str(row["patch_id"]))
            if patch.status is PatchStatus.BUILDING and patch.revision == row["building_revision"]:
                patches.transition_patch(
                    conn,
                    workspace_id=str(row["workspace_id"]),
                    patch_id=patch.patch_id,
                    to_status=PatchStatus.FAILED,
                    actor_id=None,
                    reason="candidate build failed",
                    expected_revision=patch.revision,
                    now=moment,
                )
