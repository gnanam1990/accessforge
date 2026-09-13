"""Separate human consent to reader SDK effects; no endpoint starts or configures the OS."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response, status

from accessforge_api.dependencies import require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_persistence import execution_approvals, reader_startup_consents

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/runs/{run_id}", tags=["runs"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]
_FIELDS = frozenset(
    {
        "runnerId",
        "manifestDigest",
        "desktopSessionKey",
        "runnerProfileDigest",
        "effectsDigest",
        "expiresAt",
        "dedicatedDesktopAcknowledged",
    }
)


def _target(conn: psycopg.Connection[Any], run_id: str) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM run WHERE id=%s", (as_identifier(run_id, what="run"),)
        ).fetchone()
        is None
    ):
        raise not_found()


@router.get("/reader-startup-consent/scope")
def review_reader_startup_scope(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    runner_id: Annotated[str, Query(alias="runnerId")],
) -> dict[str, Any]:
    """Exact current effects and upper expiry bound for an operator's explicit decision."""
    authorize(conn, request, workspace_id, Permission.INFRASTRUCTURE_OPERATE)
    _target(conn, run_id)
    as_identifier(runner_id, what="runner")
    try:
        result = reader_startup_consents.review_scope(
            conn, workspace_id=workspace_id, run_id=run_id, runner_id=runner_id
        )
    except (reader_startup_consents.Refused, execution_approvals.Refused):
        raise ProblemDetail(ProblemCode.CONFLICT, "reader startup scope unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


@router.get("/reader-startup-consent")
def inspect_reader_startup_consent(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    """Stored history including expiry/revocation, not current startup authority."""
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    _target(conn, run_id)
    try:
        result = reader_startup_consents.inspect(conn, run_id=run_id)
    except LookupError:
        raise not_found() from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/reader-startup-consent", status_code=status.HTTP_201_CREATED)
def issue_reader_startup_consent(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """One per-run grant; requires owner, CSRF, reviewed run If-Match and explicit scope.

    Separate RUN_EFFECTS approval must already exist. Does not grant TCC/AppleScript permission,
    start a reader, claim physical readiness or allow reissue after revocation.
    """
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.INFRASTRUCTURE_OPERATE, body, _FIELDS
    )
    expected = require_if_match(context)
    _target(conn, run_id)
    if (
        set(body) != _FIELDS
        or any(
            not isinstance(body.get(key), str) for key in _FIELDS - {"dedicatedDesktopAcknowledged"}
        )
        or body.get("dedicatedDesktopAcknowledged") is not True
    ):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact reviewed startup scope required")
    as_identifier(body["runnerId"], what="runner")
    try:
        parse_rfc3339_utc(body["expiresAt"], field="expiresAt")
    except ValueError:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "expiresAt must be a UTC timestamp"
        ) from None

    def perform() -> dict[str, Any]:
        try:
            return reader_startup_consents.issue(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                runner_id=body["runnerId"],
                actor_id=context.principal.user_id,
                expected_revision=expected,
                manifest_digest=body["manifestDigest"],
                desktop_session_key=body["desktopSessionKey"],
                runner_profile_digest=body["runnerProfileDigest"],
                effects_digest=body["effectsDigest"],
                expires_at=body["expiresAt"],
                dedicated_desktop_acknowledged=True,
            )
        except (reader_startup_consents.Refused, execution_approvals.Refused):
            raise ProblemDetail(ProblemCode.CONFLICT, "reader startup consent refused") from None

    outcome = run_idempotently(
        conn,
        context,
        route=f"POST /runs/{run_id}/reader-startup-consent",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return outcome.response or {}


@router.post("/reader-startup-consent/revocation")
def revoke_reader_startup_consent(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Withdraw the named immutable grant, not a claim that already-entered SDK work stopped.

    Exact consentId is the concurrency target; run revision changes must not prevent revocation.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.INFRASTRUCTURE_OPERATE,
        body,
        frozenset({"consentId"}),
    )
    _target(conn, run_id)
    if set(body) != {"consentId"} or not isinstance(body["consentId"], str):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact consentId required")
    as_identifier(body["consentId"], what="consent")
    try:
        result = reader_startup_consents.revoke(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            actor_id=context.principal.user_id,
            consent_id=body["consentId"],
        )
    except LookupError:
        raise not_found() from None
    except reader_startup_consents.Refused:
        raise ProblemDetail(ProblemCode.CONFLICT, "reader startup revocation refused") from None
    response.headers["Cache-Control"] = "no-store"
    return result
