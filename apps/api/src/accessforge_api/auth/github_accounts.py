"""Trusted operator bindings and session issuance after fresh GitHub authentication.

No public endpoint may call bind_account/revoke_binding on caller-supplied IDs.
Database/host access is the operator authority; a label is audit attribution,
not proof of that authority. Bind only independently verified numeric subjects to
existing local UUIDs. The separate invited-signup path below may create a new account only after
fresh provider authentication and a bound, active owner offer. Email never links an existing user.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg

from accessforge_persistence import unscoped_connection

from .github_identity import GitHubIdentityError, GitHubSubject
from .invitation_continuation import InvitationContinuation
from .membership import record_global_audit_event
from .sessions import IssuedSession, SessionError, issue_session


def _subject(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) >= 2**63:
        raise GitHubIdentityError("GitHub account binding refused")
    return int(value)


def _operator(label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", label):
        raise GitHubIdentityError("GitHub account binding refused")
    return "identity-operator:" + label


def bind_account(database_url: str, *, github_subject: str, user_id: str, operator: str) -> None:
    """Create one immutable mapping. Conflicts/revoked mappings are never overwritten."""
    subject, actor = _subject(github_subject), _operator(operator)
    try:
        user = str(uuid.UUID(user_id))
    except ValueError:
        raise GitHubIdentityError("GitHub account binding refused") from None
    try:
        with unscoped_connection(database_url) as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            existing = conn.execute(
                "SELECT user_id,revoked_at FROM github_user_identity "
                "WHERE github_subject = %s FOR UPDATE",
                (subject,),
            ).fetchone()
            if existing is not None:
                raise GitHubIdentityError("GitHub account binding refused")
            enabled = conn.execute(
                "SELECT id FROM app_user WHERE id = %s AND disabled_at IS NULL FOR SHARE",
                (user,),
            ).fetchone()
            if enabled is None:
                raise GitHubIdentityError("GitHub account binding refused")
            conn.execute(
                "INSERT INTO github_user_identity(github_subject,user_id) VALUES(%s,%s)",
                (subject, user),
            )
            record_global_audit_event(
                conn,
                action="github_identity.bound",
                target_kind="app_user",
                target_id=user,
                outcome="ALLOWED",
                actor_service=actor,
            )
    except psycopg.IntegrityError:
        raise GitHubIdentityError("GitHub account binding refused") from None


def revoke_binding(database_url: str, *, github_subject: str, operator: str) -> None:
    """Permanently revoke this binding and its sessions in one audited transaction."""
    subject, actor = _subject(github_subject), _operator(operator)
    with unscoped_connection(database_url) as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        row = conn.execute(
            "UPDATE github_user_identity SET revoked_at = clock_timestamp() "
            "WHERE github_subject = %s AND revoked_at IS NULL RETURNING user_id",
            (subject,),
        ).fetchone()
        if row is None:
            raise GitHubIdentityError("GitHub account binding refused")
        conn.execute(
            "UPDATE user_session SET revoked_at = clock_timestamp() "
            "WHERE github_subject = %s AND revoked_at IS NULL",
            (subject,),
        )
        record_global_audit_event(
            conn,
            action="github_identity.revoked",
            target_kind="app_user",
            target_id=str(row["user_id"]),
            outcome="ALLOWED",
            actor_service=actor,
        )


def _provision_invited(
    conn: psycopg.Connection[dict[str, Any]], subject: int, context: InvitationContinuation
) -> None:
    """Fresh provider identity plus a live owner offer authorizes only a NEW local account.

    Contact email is unverified metadata, never a lookup that links an existing local account.
    The caller owns the transaction and must roll back if session issuance subsequently fails.
    """
    if context.contact_email is None:
        raise GitHubIdentityError("GitHub account binding refused")
    conn.execute("SELECT set_config('accessforge.workspace_id', %s, true)", (context.workspace_id,))
    workspace = conn.execute(
        "SELECT id FROM workspace WHERE id=%s FOR UPDATE", (context.workspace_id,)
    ).fetchone()
    if workspace is None:
        raise GitHubIdentityError("GitHub account binding refused")
    offer = conn.execute(
        "SELECT created_by FROM membership_invitation WHERE workspace_id=%s AND id=%s "
        "AND github_subject=%s AND revision=1 AND accepted_at IS NULL AND revoked_at IS NULL "
        "AND expires_at>clock_timestamp() FOR UPDATE",
        (context.workspace_id, context.invitation_id, subject),
    ).fetchone()
    if offer is None:
        raise GitHubIdentityError("GitHub account binding refused")
    issuer = conn.execute(
        "SELECT m.user_id FROM workspace_membership m JOIN app_user u ON u.id=m.user_id "
        "WHERE m.workspace_id=%s AND m.user_id=%s AND m.role='OWNER' "
        "AND m.revoked_at IS NULL AND u.disabled_at IS NULL FOR UPDATE OF m FOR SHARE OF u",
        (context.workspace_id, offer["created_by"]),
    ).fetchone()
    if issuer is None:
        raise GitHubIdentityError("GitHub account binding refused")
    # Match the existing operator provisioning lock/key and case-insensitive collision rule.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(lower(%s), 0))", (context.contact_email,)
    )
    if (
        conn.execute(
            "SELECT id FROM app_user WHERE lower(email)=lower(%s)", (context.contact_email,)
        ).fetchone()
        is not None
    ):
        raise GitHubIdentityError("GitHub account binding refused")
    user = str(uuid.uuid4())
    conn.execute("INSERT INTO app_user(id,email) VALUES(%s,%s)", (user, context.contact_email))
    # UNIQUE subject/user constraints refuse races, prior/revoked bindings and any takeover.
    conn.execute(
        "INSERT INTO github_user_identity(github_subject,user_id) VALUES(%s,%s)", (subject, user)
    )
    record_global_audit_event(
        conn,
        action="github_invitation.account_created",
        target_kind="app_user",
        target_id=user,
        outcome="ALLOWED",
        actor_service="github-invitation",
    )
    if (
        conn.execute(
            "SELECT id FROM membership_invitation WHERE workspace_id=%s AND id=%s "
            "AND expires_at>clock_timestamp()",
            (context.workspace_id, context.invitation_id),
        ).fetchone()
        is None
    ):
        raise GitHubIdentityError("GitHub account binding refused")


def issue_github_session(
    database_url: str,
    identity: GitHubSubject,
    *,
    continuation: InvitationContinuation | None = None,
) -> IssuedSession:
    """Caller must have consumed browser state and freshly authenticated with GitHub.

    A GitHubSubject object is data, not a signed credential. This function is not
    an HTTP authentication endpoint and must never accept a query/body subject.
    """
    subject = _subject(identity.user_id)
    provisioned = False
    try:
        with unscoped_connection(database_url) as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            row = conn.execute(
                "SELECT user_id FROM github_user_identity WHERE github_subject = %s "
                "AND revoked_at IS NULL FOR SHARE",
                (subject,),
            ).fetchone()
            if row is None and continuation is not None and continuation.contact_email is not None:
                # Never revive a revoked identity. No account mutation for ordinary login.
                if (
                    conn.execute(
                        "SELECT user_id FROM github_user_identity WHERE github_subject=%s",
                        (subject,),
                    ).fetchone()
                    is not None
                ):
                    raise GitHubIdentityError("GitHub account binding refused")
                _provision_invited(conn, subject, continuation)
                provisioned = True
                row = conn.execute(
                    "SELECT user_id FROM github_user_identity WHERE github_subject=%s "
                    "AND revoked_at IS NULL FOR SHARE",
                    (subject,),
                ).fetchone()
            if row is None:
                raise GitHubIdentityError("GitHub account binding refused")
            session = issue_session(conn, user_id=str(row["user_id"]), github_subject=subject)
            record_global_audit_event(
                conn,
                action="session.issued",
                target_kind="session",
                target_id=session.session_id,
                outcome="ALLOWED",
                actor_user=str(row["user_id"]),
                actor_service="github-identity",
            )
            if (
                provisioned
                and continuation is not None
                and conn.execute(
                    "SELECT id FROM membership_invitation WHERE workspace_id=%s AND id=%s "
                    "AND expires_at>clock_timestamp()",
                    (continuation.workspace_id, continuation.invitation_id),
                ).fetchone()
                is None
            ):
                raise GitHubIdentityError("GitHub account binding refused")
        return session
    except (SessionError, psycopg.IntegrityError):
        raise GitHubIdentityError("GitHub account binding refused") from None
