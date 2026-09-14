"""Owner-authorized local publication recovery; no outbound GitHub capability."""

from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response

from accessforge_api.problems import ProblemCode, ProblemDetail
from accessforge_api.routes._common import as_identifier, authorize, database_url, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_orchestrator.github_connections import Refused as ConnectionRefused
from accessforge_orchestrator.github_publication_intent import read_publication_state
from accessforge_persistence import github_previews

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/github", tags=["github"])
Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


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
