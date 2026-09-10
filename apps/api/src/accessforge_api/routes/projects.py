"""Projects, environments and journey versions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.origins import OriginError, normalize_origin
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


@router.get("/members")
def list_members(workspace_id: str, request: Request, conn: Conn) -> dict[str, Any]:
    """The people who hold an active membership in this workspace.

    Needed because several records in this product name a person — who authorized a repository, who
    reviewed a finding — and a form that asked for a raw identifier would be a form nobody can fill
    in. Scoped to this workspace by row-level security, so it discloses nothing about anyone else.

    Revoked memberships are excluded rather than listed as inactive. A picker offering someone who
    can no longer be named here is offering an authorization the server will refuse.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    rows = conn.execute(
        """
        SELECT m.user_id, m.role, u.email
        FROM workspace_membership m
        JOIN app_user u ON u.id = m.user_id
        WHERE m.workspace_id = %s AND m.revoked_at IS NULL AND u.disabled_at IS NULL
        ORDER BY u.email
        """,
        (workspace_id,),
    ).fetchall()
    return {
        "items": [
            {"userId": str(r["user_id"]), "email": str(r["email"]), "role": str(r["role"])}
            for r in rows
        ]
    }


@router.post("/projects/{project_id}/environments", status_code=status.HTTP_201_CREATED)
def register_environment(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Authorize an environment for a project.

    The refusals this can produce are the point of the route, and they come from the domain rather
    than from here: an environment needs at least one explicitly permitted origin, and the observer
    and reset credentials must be different references. One identity that can both set up the answer
    and attest to it is not an independent observer, and every completion assertion in the product
    depends on that separation being real.

    Credential *references* are stored and echoed; no credential value is accepted by this route at
    all, so there is no path by which one reaches the database, a digest or an export.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PROJECT_CONFIGURE,
        body,
        frozenset(
            {
                "name",
                "allowedOrigins",
                "fixtureResetStrategy",
                "observerCredentialRef",
                "resetCredentialRef",
                "permittedEffects",
                "expiresAt",
            }
        ),
    )

    origins_body = body.get("allowedOrigins")
    if not isinstance(origins_body, list) or not origins_body:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "an environment must declare at least one allowed origin. Entering a URL is not a "
            "claim that you may test it; this list is the scope somebody authorized.",
            extra={"field": "allowedOrigins"},
            request_id=context.request_id,
        )
    try:
        origins = frozenset(normalize_origin(str(o)) for o in origins_body)
    except OriginError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            str(exc),
            extra={"field": "allowedOrigins"},
            request_id=context.request_id,
        ) from exc

    try:
        spec = projects.EnvironmentSpec(
            name=str(body["name"]),
            allowed_origins=origins,
            fixture_reset_strategy=str(body["fixtureResetStrategy"]),
            observer_credential_ref=str(body["observerCredentialRef"]),
            reset_credential_ref=str(body["resetCredentialRef"]),
            permitted_effects=frozenset(str(e) for e in body.get("permittedEffects", [])),
            expires_at=str(body["expiresAt"]),
        )
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"{exc.args[0]} is required",
            extra={"field": str(exc.args[0])},
            request_id=context.request_id,
        ) from exc

    try:
        environment_id = projects.register_environment(
            conn,
            workspace_id=workspace_id,
            project_id=project_id,
            spec=spec,
            authorized_by=context.principal.user_id,
        )
    except projects.ProjectError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc

    return {
        "environmentId": environment_id,
        "configDigest": spec.config_digest(),
        "meaning": (
            "This records the scope somebody authorized. It is not a connection test, and a "
            "successful connection would not be authorization either."
        ),
    }


@router.get("/projects/{project_id}/environments")
def list_environments(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Environments for a project, with the reasons an unusable one is unusable.

    `usable` is reported alongside the specific cause rather than instead of it. "Expired",
    "revoked" and "superseded" send an operator to three different actions, and a single false
    would send them to none of them.

    Paginated by keyset like every other listing here. An earlier version took the first 200 with no
    cursor and no indication that it had stopped — so a project with more environments than that
    lost the older ones silently, including ones still usable, and the screen presented what
    remained as the complete inventory.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT id, name, allowed_origins, fixture_reset_strategy, permitted_effects,
               config_digest, authorized_by, revoked_at, superseded_by, expires_at, created_at
        FROM environment_manifest
        WHERE project_id = %s AND (%s::uuid IS NULL OR id > %s::uuid)
        ORDER BY id
        LIMIT %s
        """,
        (project_id, after, after, size + 1),
    ).fetchall()

    now = datetime.now(UTC)
    items = []
    for r in rows[:size]:
        expires_at = r["expires_at"]
        expired = expires_at is not None and expires_at <= now
        items.append(
            {
                "environmentId": str(r["id"]),
                "name": str(r["name"]),
                "allowedOrigins": [str(o) for o in r["allowed_origins"]],
                "fixtureResetStrategy": str(r["fixture_reset_strategy"]),
                "permittedEffects": sorted(r["permitted_effects"]),
                "configDigest": str(r["config_digest"]),
                "expiresAt": None if expires_at is None else str(expires_at),
                "revoked": r["revoked_at"] is not None,
                "supersededBy": None if r["superseded_by"] is None else str(r["superseded_by"]),
                "expired": expired,
                "usable": r["revoked_at"] is None and r["superseded_by"] is None and not expired,
                # Credential references are deliberately absent. A journey author needs to know that
                # a reset strategy exists and which origins are in scope, not which credential
                # profile performs it.
            }
        )
    return {
        "items": items,
        "nextCursor": items[-1]["environmentId"] if len(rows) > size else None,
    }


@router.get("/projects/{project_id}/manifests")
def list_sealed_manifests(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The manifests this project has sealed, and what each one was sealed against.

    A run is requested against a **sealed manifest digest**, not against a journey digest. The
    manifest covers the source commit, the built artifact, the environment configuration, the
    journey, its assertions, its fixture, the runner profile, the evaluator version and the model
    configuration — all of it, together. A run queued with a journey digest in that field carries an
    identity that matches nothing, and dispatch would later refuse it for naming a different sealed
    manifest.

    This listing exists so the interface can offer a real one rather than assemble a plausible
    value. Where a project has sealed none, the honest answer is an empty list, and the screen that
    reads it must refuse to request a run rather than invent a digest.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT m.id, m.manifest_digest, m.journey_digest, m.assertion_set_digest,
               m.fixture_digest, m.runner_profile_digest, m.navigator_policy_digest,
               m.environment_config_digest, m.evaluator_version, m.model_config_digest,
               m.run_id, m.sealed_at,
               s.commit_sha, s.tree_digest, b.artifact_digest, e.name AS environment_name
          FROM sealed_manifest m
          LEFT JOIN source_snapshot s ON s.id = m.source_snapshot_id
          LEFT JOIN build_artifact b ON b.id = m.build_artifact_id
          LEFT JOIN environment_manifest e ON e.id = m.environment_manifest_id
         WHERE m.project_id = %s AND (%s::uuid IS NULL OR m.id > %s::uuid)
         ORDER BY m.id
         LIMIT %s
        """,
        (project_id, after, after, size + 1),
    ).fetchall()

    items = [
        {
            "sealedManifestId": str(r["id"]),
            "manifestDigest": str(r["manifest_digest"]),
            "journeyDigest": str(r["journey_digest"]),
            "assertionSetDigest": str(r["assertion_set_digest"]),
            "fixtureDigest": str(r["fixture_digest"]),
            "runnerProfileDigest": str(r["runner_profile_digest"]),
            "navigatorPolicyDigest": str(r["navigator_policy_digest"]),
            "environmentConfigDigest": str(r["environment_config_digest"]),
            "environmentName": None
            if r["environment_name"] is None
            else str(r["environment_name"]),
            "evaluatorVersion": str(r["evaluator_version"]),
            "modelConfigDigest": str(r["model_config_digest"]),
            "sourceCommitSha": None if r["commit_sha"] is None else str(r["commit_sha"]),
            "sourceTreeDigest": None if r["tree_digest"] is None else str(r["tree_digest"]),
            "buildArtifactDigest": (
                None if r["artifact_digest"] is None else str(r["artifact_digest"])
            ),
            # Set once a run has been sealed against it. A manifest already bound to a run is not a
            # thing to request a second run against.
            "runId": None if r["run_id"] is None else str(r["run_id"]),
            "createdAt": str(r["sealed_at"]),
        }
        for r in rows[:size]
    ]
    return {
        "items": items,
        "nextCursor": items[-1]["sealedManifestId"] if len(rows) > size else None,
        "meaning": (
            "A run is requested against one of these digests. It covers the source, the build, the "
            "environment, the journey, its assertions and fixture, the runner profile, the "
            "evaluator and the model configuration together — a journey digest alone is not a "
            "manifest and names nothing the dispatcher can match."
        ),
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
