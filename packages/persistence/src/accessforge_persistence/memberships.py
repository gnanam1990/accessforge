"""Serialized owner membership decisions, not account signup or invitations."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Role


class MembershipChangeError(ValueError):
    """No membership change was authorized or applied."""


def change_membership(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    actor_user_id: str,
    target_user_id: str,
    role: str | None,
    expected_revision: int,
    reason: str,
) -> dict[str, Any]:
    """Grant/re-role/revoke atomically within the caller's workspace-scoped transaction.

    None means revoke, not delete. Revision zero means no prior membership; restoration requires
    the actual revoked revision. UUIDs must come from a separately authorized identity workflow.
    This function never discovers users by email or issues an invitation, cookie or credential.
    HTTP callers must authenticate a live session and enforce CSRF before calling; live owner
    membership is rechecked here after serializing workspace membership decisions.
    """
    try:
        for value in (workspace_id, actor_user_id, target_user_id):
            uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MembershipChangeError("valid membership identifiers required") from exc
    if role is not None and (not isinstance(role, str) or role not in {r.value for r in Role}):
        raise MembershipChangeError("unknown membership role")
    if type(expected_revision) is not int or not 0 <= expected_revision < 9223372036854775807:
        raise MembershipChangeError("bounded nonnegative membership revision required")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise MembershipChangeError("a reason of 1–1000 characters is required")
    if (
        conn.execute("SELECT id FROM workspace WHERE id=%s FOR UPDATE", (workspace_id,)).fetchone()
        is None
    ):
        raise MembershipChangeError("workspace unavailable")
    actor = conn.execute(
        "SELECT m.role FROM workspace_membership m JOIN app_user u ON u.id=m.user_id "
        "WHERE m.workspace_id=%s AND m.user_id=%s AND m.revoked_at IS NULL "
        "AND u.disabled_at IS NULL FOR UPDATE OF m FOR SHARE OF u",
        (workspace_id, actor_user_id),
    ).fetchone()
    if actor is None or actor["role"] != "OWNER":
        raise MembershipChangeError("current owner membership required")
    # Existing identity only. A missing/disabled identity has one indistinguishable refusal.
    if (
        conn.execute(
            "SELECT id FROM app_user WHERE id=%s AND disabled_at IS NULL FOR SHARE",
            (target_user_id,),
        ).fetchone()
        is None
    ):
        raise MembershipChangeError("target account unavailable")
    previous = conn.execute(
        "SELECT role, revoked_at, revision FROM workspace_membership "
        "WHERE workspace_id=%s AND user_id=%s FOR UPDATE",
        (workspace_id, target_user_id),
    ).fetchone()
    revision = int(previous["revision"]) if previous else 0
    if revision != expected_revision:
        raise MembershipChangeError("membership revision changed; read it again")
    active = previous is not None and previous["revoked_at"] is None
    if (role is None and not active) or (active and previous and previous["role"] == role):
        raise MembershipChangeError("membership decision makes no change")
    if active and previous and previous["role"] == "OWNER" and role != "OWNER":
        owners = conn.execute(
            "SELECT m.user_id FROM workspace_membership m JOIN app_user u ON u.id=m.user_id "
            "WHERE m.workspace_id=%s AND m.role='OWNER' AND m.revoked_at IS NULL "
            "AND u.disabled_at IS NULL FOR UPDATE OF m FOR SHARE OF u",
            (workspace_id,),
        ).fetchall()
        if len(owners) < 2:
            raise MembershipChangeError("the last active owner cannot be removed or demoted")
    revision += 1
    if previous is None:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id,user_id,role,revision) "
            "VALUES (%s,%s,%s,%s)",
            (workspace_id, target_user_id, role, revision),
        )
    else:
        conn.execute(
            "UPDATE workspace_membership SET role=coalesce(%s,role), "
            "revoked_at=CASE WHEN %s THEN now() ELSE NULL END, revision=%s "
            "WHERE workspace_id=%s AND user_id=%s",
            (role, role is None, revision, workspace_id, target_user_id),
        )
    result = {"userId": target_user_id, "role": role, "revoked": role is None, "revision": revision}
    conn.execute(
        "INSERT INTO audit_event "
        "(workspace_id,actor_user,action,target_kind,target_id,outcome,detail) "
        "VALUES (%s,%s,'MEMBERSHIP_ADMINISTER','workspace_membership',%s,'ALLOWED',%s)",
        (workspace_id, actor_user_id, target_user_id, Jsonb({**result, "reason": reason.strip()})),
    )
    return result
