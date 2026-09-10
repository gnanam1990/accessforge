"""Control-plane HTTP application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .config import ApiSettings
from .health import (
    check_database,
    check_evidence_store,
    check_schema_compatibility,
    physical_runner_note,
)
from .problems import ProblemCode, ProblemDetail
from .routes import (
    exports_router,
    findings_router,
    grants_router,
    journeys_router,
    projects_router,
    runners_router,
    runs_router,
    schedules_router,
    session_router,
    settings_router,
    stream_router,
)

_log = logging.getLogger("accessforge.api")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown, with the ordering that makes a rolling deploy safe.

    **Startup does not migrate.** Two replicas starting together would run the migrator
    concurrently, and the second would either block behind the first's locks or apply a migration
    the first is mid-way through. Migration is a separate, single, deliberate step
    (`scripts/migrate.py`); this process only reports whether the schema it found is one it can
    serve. A process that migrated on boot would also make a rollback catastrophic: the old binary
    would come up and migrate *forward* again.

    **Shutdown drains rather than severs.** Uvicorn stops accepting new connections, then waits for
    in-flight requests. The thing worth being explicit about is what a killed request would cost
    here: every mutation this API performs is a single database transaction, so an interrupted
    request rolls back and the caller's `Idempotency-Key` makes the retry exact. There is no
    partially-applied state to clean up on the way out, and a shutdown hook that "tidied" anything
    would be inventing work the transaction boundary already did.
    """
    config = app.state.config
    schema = check_schema_compatibility(config.database_url)
    if not schema.ok:
        # Logged, not raised. A process that refused to start could not serve /health/ready, and an
        # orchestrator would report a crash loop rather than the actual problem. Readiness is where
        # this belongs: the process comes up, answers 503 with the reason, and takes no traffic.
        _log.warning("starting with an unservable schema: %s", schema.detail)
    yield
    _log.info("shutdown complete; in-flight requests were drained by the server")


#: Paths that legitimately answer without a session. Everything else requires one, and the contract
#: says so rather than leaving a consumer to infer it from a 401 in production.
_UNAUTHENTICATED = frozenset({"/health/live", "/health/ready", "/diagnostics", "/v1/sessions"})

#: FastAPI attaches this to every operation with a body or a path parameter, describing a 422 that
#: this application never returns: `RequestValidationError` is caught and reshaped into an RFC7807
#: document with status 400. A generated contract is only worth diffing if it describes the
#: application that exists, and a documented error shape nobody can receive is worse than none --
#: a client library generated from it would branch on a field that never arrives.
_FASTAPI_DEFAULT_VALIDATION = "422"


def _describe_contract(app: FastAPI) -> dict[str, Any]:
    """Correct the generated OpenAPI so it describes the application that actually runs.

    Two things FastAPI cannot know, both of which a consumer needs:

    **How authentication works.** Authority comes from an HttpOnly session cookie, plus a CSRF
    header on every mutating request. None of that appears in a route signature -- it is resolved
    inside `build_context` -- so the generated document declared no security at all, and a reader
    would conclude the API was open.

    **What a validation failure looks like.** Every refusal in this system is an RFC7807 problem
    document; there is exactly one handler and no route can answer any other way. The default 422
    entry described a shape that cannot occur.
    """
    from fastapi.openapi.utils import get_openapi

    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

    schema.setdefault("components", {})["securitySchemes"] = {
        "sessionCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": "accessforge_session",
            "description": (
                "HttpOnly session cookie issued by POST /v1/sessions. Not readable by script, "
                "which is why the CSRF token is a separate value rather than the same one."
            ),
        },
        "csrfHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "x-csrf-token",
            "description": (
                "Required on every mutating request. The cookie alone proves the browser has a "
                "session; it does not prove this page made the request."
            ),
        },
    }

    problem = {
        "type": "object",
        "required": ["type", "title", "status", "code", "detail", "requestId"],
        "properties": {
            "type": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "integer"},
            "code": {
                "type": "string",
                "enum": sorted(str(code) for code in ProblemCode),
                "description": "Branch on this. The prose in `detail` changes; this does not.",
            },
            "detail": {"type": "string"},
            "requestId": {"type": "string"},
        },
        "description": (
            "RFC7807. RESOURCE_NOT_FOUND is returned both for a resource that does not exist and "
            "for one belonging to another workspace: distinguishing them would let a caller "
            "discover what other tenants hold."
        ),
    }
    schema["components"].setdefault("schemas", {})["ProblemDetail"] = problem
    problem_response = {
        "description": "An RFC7807 problem document.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}
        },
    }

    for path, operations in schema.get("paths", {}).items():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            responses.pop(_FASTAPI_DEFAULT_VALIDATION, None)
            for status_code in ("400", "401", "403", "404", "409", "428", "429", "503"):
                responses.setdefault(status_code, dict(problem_response))
            operation["security"] = (
                []
                if path in _UNAUTHENTICATED
                else [{"sessionCookie": []}]
                if operation is operations.get("get")
                else [{"sessionCookie": [], "csrfHeader": []}]
            )

    # No longer referenced now that every 422 is gone, and leaving an orphan schema in a contract
    # invites somebody to generate a client type for an error that cannot happen.
    schema.get("components", {}).get("schemas", {}).pop("HTTPValidationError", None)
    schema.get("components", {}).get("schemas", {}).pop("ValidationError", None)

    app.openapi_schema = schema
    return schema


def _operation_id(route: APIRoute) -> str:
    """Name an operation after its handler, not after its URL.

    FastAPI's default builds an id from the path, producing
    `revalidate_execution_grant_v1_workspaces__workspace_id__execution_grants__grant_id__revalidations_post`
    — which a generated client then uses as a method name. Worse, it changes whenever the *path*
    changes, so moving a route renames the operation and silently breaks every consumer keyed on it.

    The handler name is stable, readable, and already unique across these routers; a collision
    raises here rather than producing two operations that quietly share an id.
    """
    return route.name


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    config = settings or ApiSettings()  # type: ignore[call-arg]
    app = FastAPI(
        title="AccessForge API",
        version="0.0.0",
        lifespan=_lifespan,
        generate_unique_id_function=_operation_id,
    )
    app.state.config = config

    @app.exception_handler(ProblemDetail)
    def _problem(_: Request, exc: ProblemDetail) -> JSONResponse:
        """Every refusal becomes an RFC7807 document.

        One handler, so no route can answer with a bare string or an unhandled exception's message.
        The exception messages in this codebase deliberately carry the specifics that help an
        operator, and an anonymous caller is not an operator.
        """
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's own validation errors, reshaped into the same document.

        The count is reported and the individual messages are not. A validation error names the
        field and often echoes the value, and echoing a value back is how a body that carried a
        token ends up in a response, a log and a bug report.
        """
        return ProblemDetail(
            ProblemCode.INVALID_INPUT,
            "the request body or parameters did not match this route's contract",
            extra={"errorCount": len(exc.errors())},
        ).to_response()

    for router in (
        session_router,
        projects_router,
        journeys_router,
        runners_router,
        runs_router,
        grants_router,
        schedules_router,
        settings_router,
        findings_router,
        exports_router,
        stream_router,
    ):
        app.include_router(router)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        """Every dependency this process needs, plus one it deliberately does not claim.

        `desktopRunner` is reported separately from `dependencies` on purpose. Anything inside
        `dependencies` contributes to the overall verdict; the runner note contributes nothing,
        because this probe has no way to establish that a machine somewhere is attached and able to
        drive a real screen reader. Folding it in either way would be a lie -- as a passing check it
        claims a desktop, and as a failing one it makes a healthy control plane look broken.
        """
        results = [
            check_database(config.database_url),
            check_schema_compatibility(config.database_url),
            check_evidence_store(config.evidence_endpoint_url),
        ]
        healthy = all(r.ok for r in results)
        return JSONResponse(
            status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "ready" if healthy else "not-ready",
                "dependencies": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
                "desktopRunner": physical_runner_note(),
            },
        )

    app.openapi = lambda: _describe_contract(app)  # type: ignore[method-assign]

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {"config": config.redacted()}

    # After every route, including the ones defined in this function. Running it earlier checked a
    # partial catalog: a later route colliding with one of these would have passed the guard and
    # then quietly shadowed the other in every generated client.
    _assert_operation_ids_are_unique(app)
    return app


def _assert_operation_ids_are_unique(app: FastAPI) -> None:
    """Two operations sharing an id is a generated client with one of them missing.

    Checked at startup rather than left to the generator, because the generator runs in CI and this
    runs everywhere — and the failure it prevents is silent: the second definition overwrites the
    first in a dict keyed by operation id, and the route simply becomes uncallable from every
    generated client while continuing to work perfectly in a browser.
    """
    seen: dict[str, str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        identifier = _operation_id(route)
        if identifier in seen:
            raise RuntimeError(
                f"two routes generate the operation id {identifier!r}: {seen[identifier]} and "
                f"{route.path}. Rename one handler; a generated client keyed on this id would "
                "silently lose one of them."
            )
        seen[identifier] = route.path
