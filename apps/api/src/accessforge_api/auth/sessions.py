"""Browser session lifecycle.

Four properties matter here, and each exists because of a specific failure:

* **Only hashes are stored.** A dump of ``user_session`` — a backup, a log, a support query — must
  not yield a usable credential.
* **Rotation on privilege change.** A session identifier that survives sign-in fixation is a
  fixation vulnerability; rotation links the new session to the old one for audit.
* **Revocation is checked on every use**, not cached. Sign-out and membership removal have to take
  effect for requests already in flight (FR-014: revocation is checked at dispatch and read
  boundaries).
* **CSRF tokens are separate from the session token** and are compared in constant time. A CSRF
  token derived from the session token protects nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

SESSION_LIFETIME = timedelta(hours=12)
SESSION_COOKIE = "accessforge_session"
CSRF_HEADER = "x-csrf-token"

# Mutations that change state need CSRF protection; safe methods do not.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SessionError(Exception):
    """Authentication failed. Deliberately uninformative to the caller about which part failed."""


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """What a successful sign-in hands to the browser.

    The raw tokens exist only in this object and in the response that sets them; the database keeps
    hashes. They are not recoverable afterwards, which is the point.
    """

    session_id: str
    session_token: str
    csrf_token: str
    expires_at: datetime


def _hash(token: str) -> str:
    """SHA-256 of a bearer-style token.

    A password hash would be wrong here: these tokens are high-entropy random values, not
    user-chosen secrets, so there is nothing to slow down a guessing attack against. What matters is
    that the stored form is not usable as a credential.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_github_binding(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    github_subject: int,
    user_id: str,
) -> None:
    # Consistent binding -> user -> session lock order for issuance, resolution,
    # rotation and revocation. Re-check after waiting, not from a cached snapshot.
    binding = conn.execute(
        "SELECT user_id FROM github_user_identity WHERE github_subject = %s "
        "AND user_id = %s AND revoked_at IS NULL FOR SHARE",
        (github_subject, user_id),
    ).fetchone()
    enabled = conn.execute(
        "SELECT id FROM app_user WHERE id = %s AND disabled_at IS NULL FOR SHARE",
        (user_id,),
    ).fetchone()
    if binding is None or enabled is None:
        raise SessionError("session is not valid")


def issue_session(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    user_id: str,
    now: datetime | None = None,
    rotated_from: str | None = None,
    github_subject: int | None = None,
) -> IssuedSession:
    """Create a session for an already-authenticated user.

    This function does not authenticate anyone. Establishing who the user is belongs to the
    configured identity provider; mixing the two would make it possible to call this with an
    unverified identifier.
    """
    moment = now or datetime.now(UTC)
    if rotated_from is not None:
        parent = conn.execute(
            "SELECT github_subject FROM user_session WHERE id = %s AND user_id = %s "
            "AND revoked_at IS NULL AND expires_at > %s",
            (rotated_from, user_id, moment),
        ).fetchone()
        if parent is None or (
            github_subject is not None and github_subject != parent["github_subject"]
        ):
            raise SessionError("session is not valid")
        github_subject = parent["github_subject"]
    if github_subject is not None:
        _require_github_binding(conn, github_subject=github_subject, user_id=user_id)
    session_id = str(uuid.uuid4())
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = moment + SESSION_LIFETIME

    conn.execute(
        """
        INSERT INTO user_session
            (id, user_id, token_hash, csrf_token_hash, created_at, last_seen_at, expires_at,
             rotated_from, github_subject)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            user_id,
            _hash(session_token),
            _hash(csrf_token),
            moment,
            moment,
            expires_at,
            rotated_from,
            github_subject,
        ),
    )
    return IssuedSession(
        session_id=session_id,
        session_token=session_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session_id: str
    user_id: str
    csrf_token_hash: str


def resolve_session(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    session_token: str | None,
    now: datetime | None = None,
) -> AuthenticatedSession:
    """Resolve a session token to a live session, or raise.

    Expiry and revocation are part of the lookup rather than a later check, so there is no window
    in which a revoked session is briefly treated as valid.
    """
    if not session_token:
        raise SessionError("no session")

    moment = now or datetime.now(UTC)
    row = conn.execute(
        """
        SELECT id, user_id, csrf_token_hash, github_subject
        FROM user_session
        WHERE token_hash = %s
          AND revoked_at IS NULL
          AND expires_at > %s
          AND (github_subject IS NULL OR EXISTS (
              SELECT 1 FROM github_user_identity g JOIN app_user u ON u.id = g.user_id
              WHERE g.github_subject = user_session.github_subject
                AND g.user_id = user_session.user_id AND g.revoked_at IS NULL
                AND u.disabled_at IS NULL
          ))
        """,
        (_hash(session_token), moment),
    ).fetchone()

    if row is None:
        # One message for absent, expired, revoked and unknown. Distinguishing them would tell a
        # caller whether a token was ever real.
        raise SessionError("session is not valid")

    if row["github_subject"] is not None:
        _require_github_binding(
            conn,
            github_subject=row["github_subject"],
            user_id=str(row["user_id"]),
        )
    conn.execute("UPDATE user_session SET last_seen_at = %s WHERE id = %s", (moment, row["id"]))
    return AuthenticatedSession(
        session_id=str(row["id"]),
        user_id=str(row["user_id"]),
        csrf_token_hash=str(row["csrf_token_hash"]),
    )


def verify_csrf(session: AuthenticatedSession, *, method: str, csrf_token: str | None) -> None:
    """Enforce CSRF protection on state-changing requests.

    Compared in constant time against the stored hash. A cookie alone is not sufficient
    authorization for a mutation, because a cookie is exactly what a cross-site request carries.
    """
    if method.upper() in SAFE_METHODS:
        return
    if not csrf_token:
        raise SessionError("missing CSRF token")
    if not hmac.compare_digest(_hash(csrf_token), session.csrf_token_hash):
        raise SessionError("CSRF token does not match this session")


def rotate_session(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    session: AuthenticatedSession,
    now: datetime | None = None,
) -> IssuedSession:
    """Replace a session with a fresh one, revoking the old.

    Called whenever the authenticated context changes. The new row records ``rotated_from`` so the
    chain remains auditable; the old token stops working immediately.
    """
    moment = now or datetime.now(UTC)
    # Validate/inherit provider provenance before taking the old session's write
    # lock, avoiding an inverted lock order against identity revocation.
    issued = issue_session(
        conn,
        user_id=session.user_id,
        now=moment,
        rotated_from=session.session_id,
    )
    revoke_session(conn, session_id=session.session_id, now=moment)
    return issued


def revoke_session(
    conn: psycopg.Connection[dict[str, Any]], *, session_id: str, now: datetime | None = None
) -> None:
    conn.execute(
        "UPDATE user_session SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
        (now or datetime.now(UTC), session_id),
    )


def revoke_all_sessions_for_user(
    conn: psycopg.Connection[dict[str, Any]], *, user_id: str, now: datetime | None = None
) -> int:
    """Revoke every live session for a user.

    Used on sign-out-everywhere and when access is withdrawn. Returns the count so a caller can
    record in the audit trail how many were actually ended.
    """
    return conn.execute(
        "UPDATE user_session SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
        (now or datetime.now(UTC), user_id),
    ).rowcount
