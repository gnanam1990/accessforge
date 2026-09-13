"""Human request/revocation only. No provider is called by these HTTP routes."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.canonical import digest
from accessforge_domain.diagnosis_requests import model_profile, validate
from accessforge_persistence import diagnoses, diagnosis_requests

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["findings"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


@router.get("/diagnosis-profile")
def read_profile(
    workspace_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    profile = model_profile()
    response.headers["Cache-Control"] = "no-store"
    return {
        "profile": profile,
        "modelProfileDigest": digest(profile),
        "meaning": "Provider calls may be billable. Reservations are not a hard financial cap. "
        "Source excerpts and retained evidence are disclosed to this provider "
        "on an explicit request.",
    }


@router.post("/runs/{run_id}/diagnosis-requests", status_code=status.HTTP_202_ACCEPTED)
def request_diagnosis(
    workspace_id: str,
    run_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(conn, request, workspace_id, Permission.RUN_REQUEST, body=body)
    as_identifier(run_id, what="run")
    if not context.idempotency_key:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "Idempotency-Key is required for diagnosis requests"
        )
    try:
        validate(body)
    except ValueError:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "exact reviewed diagnosis scope required"
        ) from None

    def perform() -> dict[str, Any]:
        try:
            return diagnosis_requests.create(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                requested_by=context.principal.user_id,
                payload=body,
            )
        except (diagnosis_requests.RequestRefused, diagnoses.DiagnosisRefused):
            raise ProblemDetail(
                ProblemCode.CONFLICT, "diagnosis request scope unavailable"
            ) from None

    result = run_idempotently(
        conn,
        context,
        route=f"POST /runs/{run_id}/diagnosis-requests",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return result.response or {}


@router.get("/diagnosis-requests/{request_id}")
def inspect_request(
    workspace_id: str,
    request_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(request_id, what="diagnosis request")
    try:
        result = diagnosis_requests.inspect(conn, request_id=request_id)
    except LookupError:
        raise not_found() from None
    except diagnoses.DiagnosisRefused:
        raise ProblemDetail(ProblemCode.CONFLICT, "diagnosis integrity unavailable") from None
    except diagnosis_requests.RequestRefused:
        raise ProblemDetail(ProblemCode.CONFLICT, "request integrity unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/diagnosis-requests/{request_id}/revocation")
def revoke_request(
    workspace_id: str,
    request_id: str,
    request: Request,
    response: Response,
    conn: Conn,
) -> dict[str, Any]:
    context = authorize(conn, request, workspace_id, Permission.RUN_REQUEST)
    as_identifier(request_id, what="diagnosis request")
    try:
        result = diagnosis_requests.revoke(
            conn,
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=context.principal.user_id,
        )
    except LookupError:
        raise not_found() from None
    except (diagnoses.DiagnosisRefused, diagnosis_requests.RequestRefused):
        raise ProblemDetail(
            ProblemCode.CONFLICT, "diagnosis request revocation unavailable"
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return result
