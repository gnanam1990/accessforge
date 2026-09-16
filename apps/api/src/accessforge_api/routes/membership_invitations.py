"""Owner invitation administration. An offer neither creates an account nor grants access."""

from __future__ import annotations

import re
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from starlette.responses import JSONResponse

from accessforge_api.auth.membership import record_audit_event
from accessforge_api.dependencies import clamp_page_size, require_if_match
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_domain.authorization.roles import Permission, Role
from accessforge_persistence import invitations

from ._common import as_body, as_identifier, authorize, workspace_scope

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/membership-invitations", tags=["membership-invitations"]
)
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


def _offer_body(body: dict[str, Any]) -> tuple[int, str, int, str]:
    if set(body) != {"githubSubject", "role", "ttlSeconds", "reason"}:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact invitation fields required")
    subject, role, ttl, reason = (
        body[k] for k in ("githubSubject", "role", "ttlSeconds", "reason")
    )
    # String wire representation preserves BIGINT identity exactly in JavaScript clients.
    if (
        not isinstance(subject, str)
        or re.fullmatch(r"[1-9][0-9]{0,18}", subject) is None
        or int(subject) >= 2**63
    ):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "numeric GitHub ID string required")
    if not isinstance(role, str) or role not in {r.value for r in Role}:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "known membership role required")
    if type(ttl) is not int or not 60 <= ttl <= 604800:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "lifetime must be 60 seconds to 7 days")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "a reason of 1–1000 characters is required")
    return int(subject), role, ttl, reason.strip()


def _public(row: dict[str, Any]) -> dict[str, Any]:
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
        "invitationId": str(row["id"]),
        "githubSubject": str(row["github_subject"]),
        "role": str(row["role"]),
        "createdBy": str(row["created_by"]),
        "reason": str(row["reason"]),
        "createdAt": row["created_at"].isoformat(),
        "expiresAt": row["expires_at"].isoformat(),
        "revision": int(row["revision"]),
        "state": state,
        "meaning": "INVITATION_OFFER_NOT_CURRENT_MEMBERSHIP_OR_ACCOUNT_PROVISIONING",
    }


def _read(conn: psycopg.Connection[Any], workspace_id: str, invitation_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id,github_subject,role,created_by,reason,created_at,expires_at,revision,"
        "accepted_at,revoked_at,expires_at <= clock_timestamp() AS expired "
        "FROM membership_invitation WHERE workspace_id=%s AND id=%s",
        (workspace_id, invitation_id),
    ).fetchone()
    if row is None:
        raise not_found()
    return _public(row)


@router.get("")
def list_invitations(
    workspace_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.MEMBERSHIP_ADMINISTER)
    cursor = as_identifier(after, what="invitation cursor") if after else None
    size = clamp_page_size(limit)
    rows = conn.execute(
        "SELECT id,github_subject,role,created_by,reason,created_at,expires_at,revision,"
        "accepted_at,revoked_at,expires_at <= clock_timestamp() AS expired "
        "FROM membership_invitation WHERE workspace_id=%s "
        "AND (%s::uuid IS NULL OR id > %s::uuid) ORDER BY id LIMIT %s",
        (workspace_id, cursor, cursor, size + 1),
    ).fetchall()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [_public(row) for row in rows[:size]],
        "nextCursor": str(rows[size - 1]["id"]) if len(rows) > size else None,
    }


@router.get("/{invitation_id}")
def read_invitation(
    workspace_id: str, invitation_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.MEMBERSHIP_ADMINISTER)
    result = _read(conn, workspace_id, as_identifier(invitation_id, what="invitation"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


def _change(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    response: Response,
    conn: psycopg.Connection[Any],
    payload: dict[str, Any] | None,
) -> dict[str, Any] | JSONResponse:
    context = authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    target = as_identifier(invitation_id, what="invitation")
    response.headers["Cache-Control"] = "no-store"
    try:
        with conn.transaction():
            if not context.principal.permits(Permission.MEMBERSHIP_ADMINISTER):
                raise ProblemDetail(ProblemCode.PERMISSION_DENIED, "owner membership required")
            expected = require_if_match(context)
            if payload is not None:
                subject, role, ttl, reason = _offer_body(as_body(payload))
                if expected != 0:
                    raise ProblemDetail(
                        ProblemCode.STALE_REVISION, "new invitation requires revision zero"
                    )
                if (
                    conn.execute(
                        "SELECT id FROM membership_invitation WHERE workspace_id=%s AND id=%s",
                        (workspace_id, target),
                    ).fetchone()
                    is not None
                ):
                    raise ProblemDetail(
                        ProblemCode.STALE_REVISION, "invitation exists; read current state"
                    )
                invitations.create_invitation(
                    conn,
                    workspace_id=workspace_id,
                    actor_user_id=context.principal.user_id,
                    invitation_id=target,
                    github_subject=subject,
                    role=role,
                    ttl_seconds=ttl,
                    reason=reason,
                )
                response.status_code = 201
            else:
                current = _read(conn, workspace_id, target)
                if expected != current["revision"] or current["state"] not in {
                    "PENDING",
                    "EXPIRED",
                }:
                    raise ProblemDetail(
                        ProblemCode.STALE_REVISION, "pending invitation revision required"
                    )
                invitations.revoke_invitation(
                    conn,
                    workspace_id=workspace_id,
                    actor_user_id=context.principal.user_id,
                    invitation_id=target,
                    expected_revision=expected,
                )
            result = _read(conn, workspace_id, target)
    except (ProblemDetail, invitations.InvitationError) as exc:
        problem = (
            exc
            if isinstance(exc, ProblemDetail)
            else ProblemDetail(
                ProblemCode.PERMISSION_DENIED, "invitation decision refused; read current state"
            )
        )
        record_audit_event(
            conn,
            workspace_id=workspace_id,
            actor_user=context.principal.user_id,
            action="MEMBERSHIP_INVITATION_ADMINISTER",
            target_kind="membership_invitation",
            target_id=target,
            outcome="DENIED",
            detail={"code": str(problem.code), "requestId": context.request_id},
        )
        problem.request_id = context.request_id
        denied = problem.to_response()
        denied.headers["Cache-Control"] = "no-store"
        return denied
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


@router.put("/{invitation_id}", response_model=None, status_code=201)
def create_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any] | JSONResponse:
    """Create an offer under a client-chosen opaque ID. Unknown outcomes require GET, not replay."""
    return _change(workspace_id, invitation_id, request, response, conn, payload)


@router.delete("/{invitation_id}", response_model=None)
def revoke_invitation(
    workspace_id: str, invitation_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any] | JSONResponse:
    """Revoke the pending offer, retaining history. Never removes an accepted membership."""
    return _change(workspace_id, invitation_id, request, response, conn, None)
