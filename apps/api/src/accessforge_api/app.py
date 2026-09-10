"""Control-plane HTTP application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    config = settings or ApiSettings()  # type: ignore[call-arg]
    app = FastAPI(title="AccessForge API", version="0.0.0", lifespan=_lifespan)
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

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {"config": config.redacted()}

    return app
