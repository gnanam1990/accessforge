"""Owner-authorized local publication recovery; no outbound GitHub capability."""

import re
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail
from accessforge_api.routes._common import (
    as_body,
    as_identifier,
    authorize,
    database_url,
    workspace_scope,
)
from accessforge_domain.authorization.roles import Permission
from accessforge_orchestrator.github_check_preview import Refused as CheckRefused
from accessforge_orchestrator.github_connections import Refused as ConnectionRefused
from accessforge_orchestrator.github_preview_approval import (
    approve_preview,
    revoke_preview_approval,
    store_preview,
)
from accessforge_orchestrator.github_publication_intent import read_publication_state
from accessforge_persistence import github_bindings, github_previews

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/github", tags=["github"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


@router.post("/publication-previews", status_code=status.HTTP_201_CREATED)
def create_github_publication_preview(
    workspace_id: str, request: Request, response: Response, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Reconstruct an exact local preview from stored evidence; never accepts a verdict."""
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.WORKSPACE_CONFIGURE,
        body,
        frozenset({"bindingId", "runId"}),
    )
    if set(body) != {"bindingId", "runId"} or any(not isinstance(v, str) for v in body.values()):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact bindingId and runId required")
    binding_id = str(UUID(as_identifier(body["bindingId"], what="repository binding")))
    run_id = str(UUID(as_identifier(body["runId"], what="run")))

    def perform() -> dict[str, Any]:
        try:
            return store_preview(
                database_url(request),
                principal=context.principal,
                binding_id=binding_id,
                run_id=run_id,
                _connection=conn,
            )
        except (github_previews.Refused, github_bindings.Refused, ConnectionRefused, CheckRefused):
            raise ProblemDetail(ProblemCode.CONFLICT, "publication preview unavailable") from None

    outcome = run_idempotently(
        conn,
        context,
        route="POST /github/publication-previews",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return outcome.response or {}


@router.post("/publication-previews/{preview_id}/approval", status_code=status.HTTP_201_CREATED)
def approve_github_publication_preview(
    workspace_id: str,
    preview_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Record a separate human decision for the reviewed digest; no remote dispatch."""
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.WORKSPACE_CONFIGURE,
        body,
        frozenset({"previewDigest"}),
    )
    identifier = str(UUID(as_identifier(preview_id, what="publication preview")))
    value = body.get("previewDigest")
    if (
        set(body) != {"previewDigest"}
        or not isinstance(value, str)
        or not re.fullmatch(r"[a-f0-9]{64}", value)
    ):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact reviewed previewDigest required")

    def perform() -> dict[str, Any]:
        try:
            approval = approve_preview(
                database_url(request),
                principal=context.principal,
                preview_id=identifier,
                expected_digest=value,
                _connection=conn,
            )
        except (
            github_previews.Refused,
            github_bindings.Refused,
            ConnectionRefused,
            CheckRefused,
            psycopg.IntegrityError,
        ):
            raise ProblemDetail(ProblemCode.CONFLICT, "publication approval unavailable") from None
        return {
            "approvalId": approval,
            "previewId": identifier,
            "previewDigest": value,
            "meaning": "RECORDED_DECISION_NOT_CURRENT_AUTHORITY",
        }

    outcome = run_idempotently(
        conn,
        context,
        route=f"POST /github/publication-previews/{identifier}/approval",
        body=body,
        perform=perform,
    )
    response.headers["Cache-Control"] = "no-store"
    return outcome.response or {}


@router.post("/publication-previews/{preview_id}/approval/revocation")
def revoke_github_publication_approval(
    workspace_id: str,
    preview_id: str,
    request: Request,
    response: Response,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Irreversible local withdrawal, not proof of remote cancellation or deletion."""
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.WORKSPACE_CONFIGURE,
        body,
        frozenset({"approvalId"}),
    )
    identifier = str(UUID(as_identifier(preview_id, what="publication preview")))
    if set(body) != {"approvalId"} or not isinstance(body["approvalId"], str):
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "exact approvalId required")
    approval_id = str(UUID(as_identifier(body["approvalId"], what="publication approval")))
    try:
        revoke_preview_approval(
            conn,
            principal=context.principal,
            preview_id=identifier,
            approval_id=approval_id,
        )
    except (github_previews.Refused, ConnectionRefused):
        raise ProblemDetail(ProblemCode.CONFLICT, "publication approval unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return {
        "approvalId": approval_id,
        "revoked": True,
        "meaning": "LOCAL_REVOCATION_NOT_REMOTE_CANCELLATION",
    }


@router.get("/publication-previews/{preview_id}/recovery")
def inspect_github_publication_recovery(
    workspace_id: str, preview_id: str, request: Request, response: Response, conn: Conn
) -> dict[str, Any]:
    """Local intent history only: UNKNOWN never becomes permission to retry publication."""
    context = authorize(conn, request, workspace_id, Permission.WORKSPACE_CONFIGURE)
    identifier = str(UUID(as_identifier(preview_id, what="publication preview")))
    try:
        result = read_publication_state(
            database_url(request),
            principal=context.principal,
            preview_id=identifier,
            _connection=conn,
        )
    except (github_previews.Refused, ConnectionRefused):
        raise ProblemDetail(ProblemCode.CONFLICT, "publication recovery unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return asdict(result)
