"""Human model-cost consent only. These endpoints never start a provider or desktop action."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.navigator_model import validate_profile
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_persistence import execution_approvals, navigator_model_calls, projects

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/runs/{run_id}", tags=["runs"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]
FIELDS = frozenset(
    {"manifestDigest", "modelProfile", "maxCalls", "expiresAt", "billableCallAcknowledged"}
)


def _target(conn: psycopg.Connection[Any], run_id: str) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM run WHERE id=%s", (as_identifier(run_id, what="run"),)
        ).fetchone()
        is None
    ):
        raise not_found()


@router.get("/navigator-model-consent/scope")
def review_navigator_model_scope(
    workspace_id: str, run_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.RUN_APPROVE)
    _target(conn, run_id)
    try:
        result = navigator_model_calls.review_scope(conn, workspace_id=workspace_id, run_id=run_id)
    except (ValueError, execution_approvals.Refused, projects.ProjectError, projects.SealError):
        raise ProblemDetail(ProblemCode.CONFLICT, "navigator model scope unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


@router.get("/navigator-model-consent")
def inspect_navigator_model_consent(
    workspace_id: str, run_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    _target(conn, run_id)
    try:
        result = navigator_model_calls.inspect_consent(conn, run_id=run_id)
    except LookupError:
        raise not_found() from None
    except ValueError:
        raise ProblemDetail(ProblemCode.CONFLICT, "navigator consent unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/navigator-model-consent", status_code=status.HTTP_201_CREATED)
def issue_navigator_model_consent(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body, FIELDS)
    if not context.idempotency_key or len(context.idempotency_key) > 200:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "bounded Idempotency-Key required")
    expected_revision = require_if_match(context)
    _target(conn, run_id)
    if (
        set(body) != FIELDS
        or body["billableCallAcknowledged"] is not True
        or not isinstance(body["modelProfile"], dict)
        or not isinstance(body["manifestDigest"], str)
        or not isinstance(body["expiresAt"], str)
        or type(body["maxCalls"]) is not int
    ):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "complete acknowledged model scope required")
    try:
        validate_profile(body["modelProfile"])
        parse_rfc3339_utc(body["expiresAt"])
    except ValueError:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "closed profile and UTC expiry required"
        ) from None

    def perform() -> dict[str, Any]:
        try:
            return navigator_model_calls.issue_consent(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                consent_id=str(uuid4()),
                actor_id=context.principal.user_id,
                expected_revision=expected_revision,
                manifest_digest=body["manifestDigest"],
                model_profile=body["modelProfile"],
                max_calls=body["maxCalls"],
                expires_at=body["expiresAt"],
                billable_call_acknowledged=True,
            )
        except (ValueError, execution_approvals.Refused, projects.ProjectError, projects.SealError):
            raise ProblemDetail(ProblemCode.CONFLICT, "navigator model consent refused") from None

    outcome = run_idempotently(
        conn,
        context,
        route=f"POST /runs/{run_id}/navigator-model-consent",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return outcome.response or {}


@router.post("/navigator-model-consent/revocation")
def revoke_navigator_model_consent(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.RUN_APPROVE, body, frozenset({"consentId"})
    )
    _target(conn, run_id)
    if set(body) != {"consentId"} or not isinstance(body["consentId"], str):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact consentId required")
    as_identifier(body["consentId"], what="consent")
    try:
        result = navigator_model_calls.revoke_consent(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            actor_id=context.principal.user_id,
            consent_id=body["consentId"],
        )
    except LookupError:
        raise not_found() from None
    except ValueError:
        raise ProblemDetail(ProblemCode.CONFLICT, "navigator consent revocation refused") from None
    response.headers["Cache-Control"] = "no-store"
    return result
