"""Trusted operator bindings and session issuance after fresh GitHub authentication.

No public endpoint may call bind_account/revoke_binding on caller-supplied IDs.
Database/host access is the operator authority; a label is audit attribution,
not proof of that authority. Bind only independently verified numeric subjects to
existing local UUIDs. No email lookup, account creation or membership mutation.
"""

from __future__ import annotations

import re
import uuid

import psycopg

from accessforge_persistence import unscoped_connection

from .github_identity import GitHubIdentityError, GitHubSubject
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


def issue_github_session(database_url: str, identity: GitHubSubject) -> IssuedSession:
    """Caller must have consumed browser state and freshly authenticated with GitHub.

    A GitHubSubject object is data, not a signed credential. This function is not
    an HTTP authentication endpoint and must never accept a query/body subject.
    """
    subject = _subject(identity.user_id)
    try:
        with unscoped_connection(database_url) as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            row = conn.execute(
                "SELECT user_id FROM github_user_identity WHERE github_subject = %s "
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
        return session
    except SessionError:
        raise GitHubIdentityError("GitHub account binding refused") from None
