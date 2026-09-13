"""Manual RUN_EFFECTS consent for an immutable canonical seal, never a patch or standing grant.

The approval target is the seal, whose revision stays zero by database invariant. The manifest
inside it binds the exact run and reserved authorization IDs, effects, budgets and expiry. A run's
operational revision changes when a lease is acquired; that is not a change to what was approved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authority import AuthorityError
from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import is_after, is_expired, to_rfc3339_utc

from . import approvals, projects


class Refused(Exception):
    """Exact execution authority is missing, changed, or no longer usable."""


def _seal(conn: psycopg.Connection[Any], sealed_manifest_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sealed_manifest WHERE id=%s FOR UPDATE", (sealed_manifest_id,)
    ).fetchone()
    if row is None:
        raise LookupError("no such seal in this workspace")
    return dict(row)


def _actor(conn: psycopg.Connection[Any], *, workspace_id: str, actor_id: str) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s",
        (workspace_id, actor_id),
    ).fetchone()
    if row is None or Permission.RUN_APPROVE not in permissions_for(Role(row["role"])):
        raise Refused("the actor no longer holds RUN_APPROVE in this workspace")


def _exact(row: dict[str, Any], *, target_digest: str, expected_revision: int) -> None:
    if row["canonical_manifest"] is None:
        raise Refused("legacy input fingerprints cannot receive execution approval")
    if (
        target_digest != row["manifest_digest"]
        or type(expected_revision) is not int
        or expected_revision != row["authorization_revision"]
    ):
        raise Refused("the reviewed seal digest or revision does not match this exact target")


def issue(
    conn: psycopg.Connection[Any],
    *,
    sealed_manifest_id: str,
    actor_id: str,
    target_digest: str,
    expected_revision: int,
    expires_at: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Issue the reserved ID once, with attribution and audit in the caller's transaction."""
    row = _seal(conn, sealed_manifest_id)
    _exact(row, target_digest=target_digest, expected_revision=expected_revision)
    moment = now or to_rfc3339_utc(datetime.now(UTC))
    manifest = projects.assert_execution_seal_current(
        conn, sealed_manifest_id=sealed_manifest_id, now=moment
    )
    _actor(conn, workspace_id=str(row["workspace_id"]), actor_id=actor_id)
    if is_expired(now=moment, expires_at=expires_at) or is_after(
        later=expires_at, earlier=manifest["expiresAt"]
    ):
        raise Refused("approval expiry must be future and no later than the execution expiry")
    if conn.execute("SELECT 1 FROM approval WHERE id=%s", (row["authorization_id"],)).fetchone():
        raise Refused(
            "this reserved approval was already issued; a new authorization needs a new seal"
        )
    approvals.record_approval(
        conn,
        workspace_id=str(row["workspace_id"]),
        scope=ApprovalScope.RUN_EFFECTS,
        actor_id=actor_id,
        target_id=sealed_manifest_id,
        target_digest=target_digest,
        expected_revision=expected_revision,
        expires_at=expires_at,
        approval_id=str(row["authorization_id"]),
    )
    _audit(conn, row=row, actor_id=actor_id, action="RUN_EFFECTS_APPROVAL_ISSUED")
    return inspect(conn, sealed_manifest_id=sealed_manifest_id)


def inspect(conn: psycopg.Connection[Any], *, sealed_manifest_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT a.* FROM sealed_manifest m JOIN approval a ON a.id=m.authorization_id "
        "WHERE m.id=%s AND m.canonical_manifest IS NOT NULL AND a.scope='RUN_EFFECTS' "
        "AND a.target_id=m.id AND a.target_digest=m.manifest_digest "
        "AND a.expected_revision=m.authorization_revision",
        (sealed_manifest_id,),
    ).fetchone()
    if row is None:
        raise LookupError("no issued execution approval for this seal")
    return {
        "approvalId": str(row["id"]),
        "scope": str(row["scope"]),
        "actorId": str(row["actor_user"]),
        "workspaceId": str(row["workspace_id"]),
        "targetId": str(row["target_id"]),
        "targetDigest": str(row["target_digest"]),
        "expectedRevision": int(row["expected_revision"]),
        "expiresAt": to_rfc3339_utc(row["expires_at"]),
        "revokedAt": None if row["revoked_at"] is None else to_rfc3339_utc(row["revoked_at"]),
        "meaning": (
            "Stored decision only; live dispatch must recheck this approval and all prerequisites."
        ),
    }


def revoke(
    conn: psycopg.Connection[Any],
    *,
    sealed_manifest_id: str,
    actor_id: str,
    target_digest: str,
    expected_revision: int,
) -> dict[str, Any]:
    row = _seal(conn, sealed_manifest_id)
    _exact(row, target_digest=target_digest, expected_revision=expected_revision)
    _actor(conn, workspace_id=str(row["workspace_id"]), actor_id=actor_id)
    inspect(conn, sealed_manifest_id=sealed_manifest_id)
    if approvals.revoke_approval(conn, approval_id=str(row["authorization_id"])):
        _audit(conn, row=row, actor_id=actor_id, action="RUN_EFFECTS_APPROVAL_REVOKED")
    return inspect(conn, sealed_manifest_id=sealed_manifest_id)


def assert_authorized(
    conn: psycopg.Connection[Any],
    *,
    sealed_manifest_id: str,
    run_id: str,
    workspace_id: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Fresh authority recheck. Not a reader, deployment, action journal, or stop proof."""
    row = _seal(conn, sealed_manifest_id)
    moment = now or to_rfc3339_utc(datetime.now(UTC))
    manifest = projects.assert_execution_seal_current(
        conn, sealed_manifest_id=sealed_manifest_id, now=moment
    )
    if manifest["runId"] != run_id or manifest["workspaceId"] != workspace_id:
        raise Refused("execution approval names a different run or workspace")
    try:
        approval = approvals.load_for_check(conn, approval_id=manifest["authorizationId"])
        approval.check(
            now=moment,
            scope=ApprovalScope.RUN_EFFECTS,
            workspace_id=workspace_id,
            target_id=sealed_manifest_id,
            target_digest=str(row["manifest_digest"]),
            current_revision=int(row["authorization_revision"]),
        )
    except (approvals.ApprovalError, AuthorityError) as exc:
        raise Refused(str(exc)) from exc
    _actor(conn, workspace_id=workspace_id, actor_id=approval.actor_id)
    return manifest


def _audit(
    conn: psycopg.Connection[Any], *, row: dict[str, Any], actor_id: str, action: str
) -> None:
    conn.execute(
        "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,outcome) "
        "VALUES(%s,%s,%s,'execution_approval',%s,'ALLOWED')",
        (row["workspace_id"], actor_id, action, str(row["authorization_id"])),
    )
