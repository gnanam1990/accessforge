"""Recipient offer read/accept for an existing verified GitHub session, not account signup.

The recipient need not belong to the workspace yet. Only their own immutable provider-bound offer
is visible; a path UUID or caller-supplied identity is never sufficient authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from starlette.responses import JSONResponse

from accessforge_api.auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SessionError,
    resolve_session,
    verify_csrf,
)
from accessforge_api.auth.membership import record_audit_event
from accessforge_api.auth.sessions import AuthenticatedSession, rotate_session
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.telemetry import resolve_request_id
from accessforge_persistence import invitations, rate_limits, unscoped_connection
from accessforge_persistence.memberships import MembershipChangeError

from ._common import as_identifier, workspace_scope
from .session import _set_session_cookies

router = APIRouter(
    prefix="/v1/invitation-offers/{workspace_id}/{invitation_id}", tags=["invitation-acceptance"]
)
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


def _recipient(conn: psycopg.Connection[Any], request: Request) -> tuple[AuthenticatedSession, int]:
    try:
        session = resolve_session(conn, session_token=request.cookies.get(SESSION_COOKIE))
    except SessionError as exc:
        raise ProblemDetail(
            ProblemCode.NOT_AUTHENTICATED, "verified GitHub session required"
        ) from exc
    try:
        verify_csrf(session, method=request.method, csrf_token=request.headers.get(CSRF_HEADER))
    except SessionError as exc:
        raise ProblemDetail(ProblemCode.CSRF_REQUIRED, "valid session CSRF token required") from exc
    row = conn.execute(
        "SELECT github_subject FROM user_session WHERE id=%s AND user_id=%s "
        "AND revoked_at IS NULL AND expires_at > clock_timestamp() FOR SHARE",
        (session.session_id, session.user_id),
    ).fetchone()
    if row is None or row["github_subject"] is None:
        # A local-development session for a bound account is NOT a verified provider session.
        raise ProblemDetail(ProblemCode.NOT_AUTHENTICATED, "verified GitHub session required")
    return session, int(row["github_subject"])


def _limit(request: Request, user_id: str) -> None:
    """Durable global principal admission; never charge a guessed workspace's bucket."""
    config = request.app.state.config
    per_minute = config.rate_limit_principal_per_minute
    with unscoped_connection(config.database_url) as conn:
        decision = rate_limits.consume(
            conn,
            scope_kind="PRINCIPAL",
            scope_id=user_id,
            capacity=max(1, int(per_minute * config.rate_limit_burst_multiplier)),
            refill_per_second=per_minute / 60,
            now=datetime.now(UTC),
        )
    if not decision.allowed:
        raise ProblemDetail(
            ProblemCode.RATE_LIMITED,
            "too many invitation acceptance requests",
            extra={
                "scope": "PRINCIPAL",
                "limitPerMinute": decision.limit_per_minute,
                "burstCapacity": decision.burst_capacity,
                "retryAfterSeconds": decision.retry_after_seconds,
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def _offer(
    conn: psycopg.Connection[Any], workspace_id: str, invitation_id: str, subject: int
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT i.role,i.reason,i.revision,i.expires_at,i.accepted_at,i.revoked_at,"
        "i.expires_at <= clock_timestamp() AS expired,w.name "
        "FROM membership_invitation i JOIN workspace w ON w.id=i.workspace_id "
        "WHERE i.workspace_id=%s AND i.id=%s AND i.github_subject=%s",
        (workspace_id, invitation_id, subject),
    ).fetchone()
    if row is None:
        raise not_found()
    state = (
        "ACCEPTED"
        if row["accepted_at"] is not None
        else "REVOKED"
        if row["revoked_at"] is not None
        else "EXPIRED"
        if row["expired"]
        else "PENDING"
    )
    return {
        "workspaceId": workspace_id,
        "invitationId": invitation_id,
        "workspaceName": str(row["name"]),
        "role": str(row["role"]),
        "reason": str(row["reason"]),
        "revision": int(row["revision"]),
        "expiresAt": row["expires_at"].isoformat(),
        "state": state,
        "meaning": "OFFER_STATE_NOT_CURRENT_MEMBERSHIP_AUTHORITY",
    }


def _decision(payload: dict[str, Any], if_match: str | None) -> int:
    if set(payload) != {"accept"} or payload["accept"] is not True:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "explicit acceptance only; no identity fields"
        )
    if if_match is None:
        raise ProblemDetail(ProblemCode.IF_MATCH_REQUIRED, "read the invitation revision first")
    if if_match not in {'"1"', "1"}:
        raise ProblemDetail(ProblemCode.STALE_REVISION, "pending invitation revision one required")
    return 1


@router.get("")
def read_offer(
    workspace_id: str, invitation_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    _, subject = _recipient(conn, request)
    as_identifier(workspace_id, what="workspace")
    as_identifier(invitation_id, what="invitation")
    result = _offer(conn, workspace_id, invitation_id, subject)
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


@router.post("/accept", response_model=None)
def accept_offer(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any] | JSONResponse:
    session, subject = _recipient(conn, request)
    _limit(request, session.user_id)
    as_identifier(workspace_id, what="workspace")
    as_identifier(invitation_id, what="invitation")
    # Do not let arbitrary IDs append another workspace's audit. Only a subject-bound offer
    # authorizes the recipient's tenant-scoped acceptance/denial event.
    _offer(conn, workspace_id, invitation_id, subject)
    request_id = resolve_request_id(request)
    try:
        with conn.transaction():
            expected = _decision(payload, request.headers.get("If-Match"))
            membership = invitations.accept_invitation(
                conn,
                workspace_id=workspace_id,
                invitation_id=invitation_id,
                authenticated_user_id=session.user_id,
                authenticated_github_subject=subject,
                expected_revision=expected,
            )
            rotated = rotate_session(conn, session=session)
            record_audit_event(
                conn,
                workspace_id=workspace_id,
                actor_user=session.user_id,
                action="MEMBERSHIP_INVITATION_SESSION_ROTATED",
                target_kind="user_session",
                target_id=rotated.session_id,
                outcome="ALLOWED",
                detail={"previousSessionId": session.session_id},
            )
    except (ProblemDetail, invitations.InvitationError, MembershipChangeError, SessionError) as exc:
        problem = (
            exc
            if isinstance(exc, ProblemDetail)
            else ProblemDetail(
                ProblemCode.PERMISSION_DENIED, "invitation acceptance refused; read current state"
            )
        )
        record_audit_event(
            conn,
            workspace_id=workspace_id,
            actor_user=session.user_id,
            action="MEMBERSHIP_INVITATION_ACCEPT",
            target_kind="membership_invitation",
            target_id=invitation_id,
            outcome="DENIED",
            detail={"code": str(problem.code)},
        )
        problem.request_id = request_id
        denied = problem.to_response()
        denied.headers["Cache-Control"] = "no-store"
        return denied
    _set_session_cookies(
        response,
        session_token=rotated.session_token,
        csrf_token=rotated.csrf_token,
        secure=request.app.state.config.environment != "local",
        expires=rotated.expires_at,
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "workspaceId": workspace_id,
        "invitationId": invitation_id,
        "membership": membership,
        "sessionRotated": True,
    }
