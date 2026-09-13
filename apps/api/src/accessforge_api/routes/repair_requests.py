"""Explicit human repair consent and readback only; no model or build dispatch."""

from collections.abc import Callable
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response

from accessforge_api.dependencies import run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.repair_requests import validate
from accessforge_persistence import evaluations, repair_deliveries, repair_requests
from accessforge_persistence.diagnosis_requests import operation_digest

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["patches"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


def _read(action: Callable[[], dict[str, Any]], response: Response) -> dict[str, Any]:
    try:
        result = action()
    except LookupError:
        raise not_found() from None
    except (
        repair_requests.RequestRefused,
        repair_deliveries.DeliveryRefused,
        evaluations.EvaluationError,
    ):
        raise ProblemDetail(
            ProblemCode.CONFLICT, "repair request scope or integrity unavailable"
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/findings/{finding_id}/repair-options")
def repair_options(
    workspace_id: str,
    finding_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    diagnosis_id: Annotated[str, Query(alias="diagnosisId")],
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(finding_id, what="finding")
    as_identifier(diagnosis_id, what="diagnosis")
    return _read(
        lambda: repair_requests.preview(conn, finding_id=finding_id, diagnosis_id=diagnosis_id),
        response,
    )


@router.post("/findings/{finding_id}/repair-requests", status_code=202)
def request_repair(
    workspace_id: str,
    finding_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(conn, request, workspace_id, Permission.PROJECT_CONFIGURE, body=body)
    as_identifier(finding_id, what="finding")
    try:
        validate(body)
        key = context.idempotency_key or ""
        operation_digest(key)
    except ValueError:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "exact repair scope, consent and Idempotency-Key required"
        ) from None

    def perform() -> dict[str, Any]:
        return _read(
            lambda: repair_requests.create(
                conn,
                workspace_id=workspace_id,
                finding_id=finding_id,
                requested_by=context.principal.user_id,
                payload=body,
                idempotency_key=key,
            ),
            response,
        )

    result = run_idempotently(
        conn,
        context,
        route=f"POST /findings/{finding_id}/repair-requests",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/findings/{finding_id}/repair-requests/operation")
def recover_repair(
    workspace_id: str,
    finding_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    operation_key: Annotated[str, Query(alias="operationKey", min_length=1, max_length=200)],
) -> dict[str, Any]:
    context = authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(finding_id, what="finding")
    return _read(
        lambda: repair_requests.recover(
            conn,
            workspace_id=workspace_id,
            finding_id=finding_id,
            actor_id=context.principal.user_id,
            idempotency_key=operation_key,
        ),
        response,
    )


@router.get("/repair-requests/{request_id}")
def inspect_repair(
    workspace_id: str, request_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(request_id, what="repair request")
    return _read(lambda: repair_requests.inspect(conn, request_id=request_id), response)


@router.post("/repair-requests/{request_id}/revocation")
def revoke_repair(
    workspace_id: str, request_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    context = authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(request_id, what="repair request")
    return _read(
        lambda: repair_requests.revoke(
            conn,
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=context.principal.user_id,
        ),
        response,
    )
