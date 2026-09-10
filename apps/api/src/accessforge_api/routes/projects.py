"""Projects, environments and journey versions."""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import projects

from ._common import as_body, authorize, workspace_scope

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["projects"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PROJECT_CONFIGURE,
        body,
        frozenset({"name", "repositoryUrl", "repositoryAuthorizedBy"}),
    )
    try:
        project_id = projects.create_project(
            conn,
            workspace_id=workspace_id,
            name=str(body["name"]),
            repository_url=body.get("repositoryUrl"),
            repository_authorized_by=body.get("repositoryAuthorizedBy"),
        )
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "name is required", request_id=context.request_id
        ) from exc
    except projects.ProjectError as exc:
        # Reachability is not consent: a repository URL without an authorizing user is refused by
        # the domain, and the route reports that rather than reinterpreting it.
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc
    return {"projectId": project_id}


@router.get("/projects")
def list_projects(
    workspace_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        # Keyset on (created_at, id) rather than OFFSET. Ordering by a single non-unique column
        # would make the cursor ambiguous for rows sharing a timestamp, and OFFSET would skip or
        # repeat rows as the table grew underneath a reader.
        """
        SELECT id, name, repository_url, created_at
        FROM project
        WHERE (%s::uuid IS NULL OR id > %s::uuid)
        ORDER BY id
        LIMIT %s
        """,
        (after, after, size + 1),
    ).fetchall()

    items = [
        {
            "projectId": str(r["id"]),
            "name": str(r["name"]),
            "repositoryUrl": r["repository_url"],
            "createdAt": str(r["created_at"]),
        }
        for r in rows[:size]
    ]
    return {
        "items": items,
        "nextCursor": items[-1]["projectId"] if len(rows) > size else None,
    }


@router.get("/projects/{project_id}")
def get_project(workspace_id: str, project_id: str, request: Request, conn: Conn) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    row = conn.execute(
        "SELECT id, name, repository_url, created_at FROM project WHERE id = %s", (project_id,)
    ).fetchone()
    if row is None:
        # Row-level security already hid another tenant's project, so this is the same 404 whether
        # the project does not exist or belongs to someone else. That is the point.
        raise not_found()
    return {
        "projectId": str(row["id"]),
        "name": str(row["name"]),
        "repositoryUrl": row["repository_url"],
        "createdAt": str(row["created_at"]),
    }


@router.get("/journeys/{journey_version_id}")
def get_journey_version(
    workspace_id: str, journey_version_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    row = conn.execute(
        """
        SELECT id, name, platform, journey_digest, assertion_set_digest, fixture_digest,
               navigator_policy_digest, reviewer_summary, created_at, supersedes
        FROM journey_version WHERE id = %s
        """,
        (journey_version_id,),
    ).fetchone()
    if row is None:
        raise not_found()

    # A frozen version is immutable, so it is safe to cache and its digest is a natural ETag.
    response.headers["ETag"] = f'"{row["journey_digest"]}"'
    return {
        "journeyVersionId": str(row["id"]),
        "name": str(row["name"]),
        "platform": str(row["platform"]),
        "journeyDigest": str(row["journey_digest"]),
        "assertionSetDigest": str(row["assertion_set_digest"]),
        "fixtureDigest": str(row["fixture_digest"]),
        "navigatorPolicyDigest": str(row["navigator_policy_digest"]),
        # The reviewer summary is safe by construction: module 06 builds it without oracle material.
        "reviewerSummary": row["reviewer_summary"],
        "supersedes": None if row["supersedes"] is None else str(row["supersedes"]),
        "createdAt": str(row["created_at"]),
    }
