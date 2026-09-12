"""Projects, environments and journey versions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.origins import OriginError, normalize_origin
from accessforge_persistence import projects
from accessforge_persistence.source_intake import SourceIdentity

from ._common import as_body, as_identifier, authorize, workspace_scope

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["projects"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope, scope="function")]


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


def _assert_project_visible(conn: psycopg.Connection[Any], *, project_id: str) -> None:
    """The uniform 404 for a project this caller cannot see.

    Row-level security already hides another tenant's project, so this answers the same way whether
    the project does not exist or belongs to somebody else. Checked before the body is acted on, so
    a caller cannot learn that a project exists by watching which validation error comes back.
    """
    row = conn.execute(
        "SELECT 1 FROM project WHERE id = %s",
        (as_identifier(project_id, what="projectId"),),
    ).fetchone()
    if row is None:
        raise not_found()


BUILD_FIELDS = frozenset(
    {
        "commitSha",
        "treeDigest",
        "dirty",
        "dirtyPaths",
        "requestedRevision",
        "artifactDigest",
        "identityObservable",
    }
)

SEAL_FIELDS = frozenset(
    {
        "buildId",
        "environmentId",
        "journeyDigest",
        "assertionSetDigest",
        "fixtureDigest",
        "runnerProfileDigest",
        "navigatorPolicyDigest",
        "evaluatorVersion",
        "modelConfigDigest",
    }
)


@router.post("/projects/{project_id}/builds", status_code=status.HTTP_201_CREATED)
def register_build(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Record what was built, and from which source.

    One operation for the source snapshot and the artifact, because they are one fact: an artifact
    digest with no source identity is an artifact nobody can trace, and a source identity with no
    artifact is a commit nobody built. Recording them separately would allow both halves to exist
    apart, and the half that goes missing is always the one a reader needed.

    **A dirty tree is recorded, not refused.** Local development is a legitimate case in E0. What is
    forbidden is describing it as clean afterwards, so `dirty` and the count of differing paths are
    stored and travel into every seal built on this build.

    **`identityObservable: false` is accepted and is a claim about the deployment, not the build.**
    It means the environment cannot prove which artifact it is serving. Such a run may still
    execute; it simply cannot make a fully verified provenance claim, and the limitation stays
    visible rather than being replaced with a plausible digest.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PROJECT_CONFIGURE,
        body=body,
        allowed_fields=BUILD_FIELDS,
    )
    _assert_project_visible(conn, project_id=project_id)

    missing = sorted({"commitSha", "treeDigest", "requestedRevision", "artifactDigest"} - set(body))
    if missing:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"missing required field(s): {', '.join(missing)}. A build records both where the "
            "source came from and what was produced from it; neither half is optional, because "
            "either one alone is untraceable.",
            request_id=context.request_id,
        )

    dirty_paths = body.get("dirtyPaths", [])
    if not isinstance(dirty_paths, list):
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "dirtyPaths must be an array. A bare string is iterable and would be recorded as one "
            "path per character.",
            request_id=context.request_id,
        )

    # `dirty` and `dirtyPaths` were read independently, so `{"dirtyPaths": ["a.ts"]}` with `dirty`
    # omitted recorded a snapshot saying a *clean* tree had one differing path -- and that record
    # travels into every seal built on the build, which is exactly what this route's docstring
    # forbids. Two halves of one fact cannot be supplied separately:
    #
    #   * omitted, it is derived -- paths present means dirty;
    #   * supplied and contradicting the paths, it is refused rather than reconciled. Silently
    #     correcting a caller who said "clean" would hide a disagreement about the thing the whole
    #     provenance chain rests on.
    if "dirty" in body:
        if bool(body["dirty"]) != bool(dirty_paths):
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                f"dirty is {bool(body['dirty'])} and dirtyPaths has {len(dirty_paths)} entry/ies; "
                "they describe the same fact and disagree. A snapshot recording a clean tree with "
                "differing paths -- or a dirty tree with none -- would be carried into every seal "
                "built on this build. Send one or the other, not a contradiction.",
                request_id=context.request_id,
            )
        dirty = bool(body["dirty"])
    else:
        dirty = bool(dirty_paths)

    def perform() -> dict[str, Any]:
        identity = SourceIdentity(
            commit_sha=str(body["commitSha"]),
            tree_digest=str(body["treeDigest"]),
            dirty=dirty,
            dirty_paths=tuple(str(p) for p in dirty_paths),
        )
        try:
            snapshot_id = projects.record_source_snapshot(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                identity=identity,
                requested_revision=str(body["requestedRevision"]),
            )
            artifact_id = projects.record_build_artifact(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                source_snapshot_id=snapshot_id,
                artifact_digest=str(body["artifactDigest"]),
                identity_observable=bool(body.get("identityObservable", True)),
            )
        except (projects.ProjectError, ValueError) as exc:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
            ) from exc

        return {
            "buildId": artifact_id,
            "sourceSnapshotId": snapshot_id,
            "commitSha": identity.commit_sha,
            "treeDigest": identity.tree_digest,
            "dirty": identity.dirty,
            "dirtyPathCount": len(identity.dirty_paths),
            "artifactDigest": str(body["artifactDigest"]),
            "identityObservable": bool(body.get("identityObservable", True)),
            "meaning": (
                "Recorded as observed. A dirty tree stays dirty in every seal built on this build, "
                "and identityObservable false means the deployment cannot prove which artifact it "
                "serves -- not that it serves the one named here."
            ),
        }

    outcome = run_idempotently(
        conn, context, route="POST /projects/builds", body=body, perform=perform
    )
    return outcome.response or {}


@router.post("/projects/{project_id}/seals", status_code=status.HTTP_201_CREATED)
def seal_manifest(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Seal a manifest over every input a run's identity depends on.

    This is the route a run is requested against. `POST /runs` refuses a digest nothing sealed, so
    until this existed a manifest could only be produced by writing SQL — which meant the product's
    central identity step had no surface at all, and the check that would have revealed it was
    missing.

    The environment is validated first by `seal_run`: sealing against a revoked, expired or
    superseded environment would produce a manifest that looked authoritative and was never
    authorized.

    A digest is deliberately **not** unique. Two seals over identical inputs share one, which is how
    a baseline and a candidate are shown to differ only by an approved patch (INV-04). Sealing twice
    is therefore not an error, and a caller who wants one seal per attempt supplies an
    `Idempotency-Key`.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.PROJECT_CONFIGURE,
        body=body,
        allowed_fields=SEAL_FIELDS,
    )
    _assert_project_visible(conn, project_id=project_id)

    missing = sorted(SEAL_FIELDS - set(body))
    if missing:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"missing required field(s): {', '.join(missing)}. Every input is required: a "
            "manifest that omitted one would describe a run identity not covering it, and the gap "
            "would surface as two runs looking identical while differing in the field nobody "
            "sealed.",
            request_id=context.request_id,
        )

    def perform() -> dict[str, Any]:
        # Both inputs are checked to belong to *this* project, not merely to this workspace.
        # Row-level security scopes by workspace and the foreign keys are composite on
        # (id, workspace_id), so neither stops a caller authorized for project A from sealing
        # project B's artifact and environment into a manifest whose project_id is A. Runs requested
        # with that digest would then carry provenance naming a project that never built the
        # artifact -- a false claim of exactly the kind this chain exists to make impossible.
        build = conn.execute(
            "SELECT id, source_snapshot_id, project_id FROM build_artifact WHERE id = %s",
            (as_identifier(str(body["buildId"]), what="buildId"),),
        ).fetchone()
        if build is None:
            raise not_found()
        if str(build["project_id"]) != project_id:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                "that build belongs to a different project. A seal describes one project's inputs; "
                "naming another's artifact would record provenance for a build this project never "
                "produced.",
                request_id=context.request_id,
            )

        environment_row = conn.execute(
            "SELECT project_id FROM environment_manifest WHERE id = %s",
            (as_identifier(str(body["environmentId"]), what="environmentId"),),
        ).fetchone()
        if environment_row is None:
            raise not_found()
        if str(environment_row["project_id"]) != project_id:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                "that environment belongs to a different project. An environment is authorized for "
                "one project, and sealing against another's would claim an authorization nobody "
                "gave.",
                request_id=context.request_id,
            )

        try:
            sealed = projects.seal_run(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                source_snapshot_id=str(build["source_snapshot_id"]),
                build_artifact_id=str(build["id"]),
                environment_manifest_id=as_identifier(
                    str(body["environmentId"]), what="environmentId"
                ),
                inputs=projects.SealInputs(
                    journey_digest=str(body["journeyDigest"]),
                    assertion_set_digest=str(body["assertionSetDigest"]),
                    fixture_digest=str(body["fixtureDigest"]),
                    runner_profile_digest=str(body["runnerProfileDigest"]),
                    navigator_policy_digest=str(body["navigatorPolicyDigest"]),
                    evaluator_version=str(body["evaluatorVersion"]),
                    model_config_digest=str(body["modelConfigDigest"]),
                ),
            )
        except projects.SealError as exc:
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
            ) from exc
        except projects.ProjectError as exc:
            # A revoked, expired or superseded environment. 409 rather than 400: the body is
            # well-formed and the state it names is not usable.
            raise ProblemDetail(
                ProblemCode.CONFLICT, str(exc), request_id=context.request_id
            ) from exc

        return {
            "sealedManifestId": sealed.sealed_manifest_id,
            "manifestDigest": sealed.manifest_digest,
            "requestRunWith": sealed.manifest_digest,
            "meaning": (
                "This digest covers the source commit, the built artifact, the environment "
                "configuration, the journey, its assertions, its fixture, the runner profile, the "
                "evaluator version and the model configuration -- together. Request a run against "
                "it. Two seals over identical inputs share a digest by design; that is how a "
                "baseline and a candidate are shown to differ only by an approved patch."
            ),
        }

    outcome = run_idempotently(
        conn, context, route="POST /projects/seals", body=body, perform=perform
    )
    return outcome.response or {}
