"""Workspace-scoped GitHub invitations. No provider calls, account signup or bearer links.

HTTP integration must authenticate/CSRF-check the caller. For acceptance, subject and user ID
must come from freshly verified provider identity or a live provider-bound session, never a
caller-supplied identity claim. UUID knowledge alone conveys no invitation authority.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Role

from .memberships import change_membership


class InvitationError(ValueError):
    """Invitation decision refused; caller must roll back its transaction/savepoint."""


def _identifiers(*values: str) -> None:
    try:
        for value in values:
            uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvitationError("valid invitation identifiers required") from exc


def _subject(value: int) -> None:
    if type(value) is not int or not 0 < value < 2**63:
        raise InvitationError("positive numeric GitHub identity required")


def _workspace(conn: psycopg.Connection[dict[str, Any]], workspace_id: str) -> None:
    if (
        conn.execute("SELECT id FROM workspace WHERE id=%s FOR UPDATE", (workspace_id,)).fetchone()
        is None
    ):
        raise InvitationError("workspace unavailable")


def _owner(conn: psycopg.Connection[dict[str, Any]], workspace_id: str, user_id: str) -> None:
    row = conn.execute(
        "SELECT m.role FROM workspace_membership m JOIN app_user u ON u.id=m.user_id "
        "WHERE m.workspace_id=%s AND m.user_id=%s AND m.revoked_at IS NULL "
        "AND u.disabled_at IS NULL FOR UPDATE OF m FOR SHARE OF u",
        (workspace_id, user_id),
    ).fetchone()
    if row is None or row["role"] != "OWNER":
        raise InvitationError("current owner membership required")


def _audit(
    conn: psycopg.Connection[dict[str, Any]],
    workspace_id: str,
    actor: str,
    invitation_id: str,
    action: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_event "
        "(workspace_id,actor_user,action,target_kind,target_id,outcome,detail) "
        "VALUES (%s,%s,%s,'membership_invitation',%s,'ALLOWED',%s)",
        (workspace_id, actor, action, invitation_id, Jsonb({})),
    )


def create_invitation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    actor_user_id: str,
    invitation_id: str,
    github_subject: int,
    role: str,
    ttl_seconds: int,
    reason: str,
) -> dict[str, Any]:
    """Create an explicit owner offer, not a membership. No identity lookup by email.

    The numeric subject may not yet have a local account. Identity provisioning is a separate
    trusted workflow. A repeated ID is a conflict, not permission to refresh an old offer or expiry.
    """
    _identifiers(workspace_id, actor_user_id, invitation_id)
    _subject(github_subject)
    if not isinstance(role, str) or role not in {r.value for r in Role}:
        raise InvitationError("known membership role required")
    if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 7 * 86400:
        raise InvitationError("invitation lifetime must be 60 seconds to 7 days")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise InvitationError("a reason of 1–1000 characters is required")
    _workspace(conn, workspace_id)
    _owner(conn, workspace_id, actor_user_id)
    if (
        conn.execute(
            "SELECT id FROM membership_invitation WHERE workspace_id=%s AND id=%s",
            (workspace_id, invitation_id),
        ).fetchone()
        is not None
    ):
        raise InvitationError("invitation already exists; read its state")
    row = conn.execute(
        "WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at) "
        "INSERT INTO membership_invitation "
        "(workspace_id,id,github_subject,role,created_by,reason,created_at,expires_at) "
        "SELECT %s,%s,%s,%s,%s,%s,at,at+(%s * interval '1 second') FROM moment "
        "RETURNING id,role,revision,expires_at",
        (
            workspace_id,
            invitation_id,
            github_subject,
            role,
            actor_user_id,
            reason.strip(),
            ttl_seconds,
        ),
    ).fetchone()
    assert row is not None
    _audit(conn, workspace_id, actor_user_id, invitation_id, "MEMBERSHIP_INVITATION_CREATED")
    return row


def revoke_invitation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    actor_user_id: str,
    invitation_id: str,
    expected_revision: int,
) -> None:
    _identifiers(workspace_id, actor_user_id, invitation_id)
    if type(expected_revision) is not int or expected_revision != 1:
        raise InvitationError("pending invitation revision required")
    _workspace(conn, workspace_id)
    _owner(conn, workspace_id, actor_user_id)
    row = conn.execute(
        "UPDATE membership_invitation SET revoked_at=clock_timestamp(),revision=revision+1 "
        "WHERE workspace_id=%s AND id=%s AND revision=%s "
        "AND revoked_at IS NULL AND accepted_at IS NULL RETURNING id",
        (workspace_id, invitation_id, expected_revision),
    ).fetchone()
    if row is None:
        raise InvitationError("pending invitation unavailable")
    _audit(conn, workspace_id, actor_user_id, invitation_id, "MEMBERSHIP_INVITATION_REVOKED")


def accept_invitation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    invitation_id: str,
    authenticated_user_id: str,
    authenticated_github_subject: int,
    expected_revision: int,
) -> dict[str, Any]:
    """Accept atomically even if a caller catches a refusal inside its outer transaction."""
    with conn.transaction():
        return _accept_invitation(
            conn,
            workspace_id=workspace_id,
            invitation_id=invitation_id,
            authenticated_user_id=authenticated_user_id,
            authenticated_github_subject=authenticated_github_subject,
            expected_revision=expected_revision,
        )


def _accept_invitation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    invitation_id: str,
    authenticated_user_id: str,
    authenticated_github_subject: int,
    expected_revision: int,
) -> dict[str, Any]:
    """Atomic first membership and acceptance; never restore or overwrite an existing relationship.

    Call only within a workspace-scoped transaction after identity and CSRF verification. Recheck
    binding, account, issuer ownership and expiry after locks. A replay is refused, not a new grant.
    Denials are rolled back and audited by the HTTP caller; successes commit with membership audit.
    """
    _identifiers(workspace_id, invitation_id, authenticated_user_id)
    _subject(authenticated_github_subject)
    if type(expected_revision) is not int or expected_revision != 1:
        raise InvitationError("pending invitation revision required")
    _workspace(conn, workspace_id)
    binding = conn.execute(
        "SELECT user_id FROM github_user_identity WHERE github_subject=%s AND user_id=%s "
        "AND revoked_at IS NULL FOR SHARE",
        (authenticated_github_subject, authenticated_user_id),
    ).fetchone()
    if binding is None:
        raise InvitationError("verified GitHub binding unavailable")
    row = conn.execute(
        "SELECT * FROM membership_invitation WHERE workspace_id=%s AND id=%s "
        "AND github_subject=%s AND revision=%s AND revoked_at IS NULL "
        "AND accepted_at IS NULL FOR UPDATE",
        (workspace_id, invitation_id, authenticated_github_subject, expected_revision),
    ).fetchone()
    if row is None:
        raise InvitationError("pending invitation unavailable")
    # The membership service rechecks issuer ownership under the same workspace lock. Its exact
    # zero revision prevents invitation acceptance from undoing any earlier revocation/role change.
    _owner(conn, workspace_id, str(row["created_by"]))
    result = change_membership(
        conn,
        workspace_id=workspace_id,
        actor_user_id=str(row["created_by"]),
        target_user_id=authenticated_user_id,
        role=str(row["role"]),
        expected_revision=0,
        reason=str(row["reason"]),
    )
    accepted = conn.execute(
        "UPDATE membership_invitation SET accepted_at=clock_timestamp(),accepted_by=%s,revision=2 "
        "WHERE workspace_id=%s AND id=%s AND expires_at > clock_timestamp() RETURNING id",
        (authenticated_user_id, workspace_id, invitation_id),
    ).fetchone()
    if accepted is None:
        # Rollback is mandatory: the tentative membership and its audit must not survive expiry.
        raise InvitationError("invitation expired")
    _audit(
        conn, workspace_id, authenticated_user_id, invitation_id, "MEMBERSHIP_INVITATION_ACCEPTED"
    )
    return result
