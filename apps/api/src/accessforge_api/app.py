"""Control-plane HTTP application."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import ApiSettings
from .health import check_database, check_evidence_store
from .problems import ProblemCode, ProblemDetail
from .routes import (
    exports_router,
    findings_router,
    projects_router,
    runners_router,
    runs_router,
    session_router,
)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    config = settings or ApiSettings()  # type: ignore[call-arg]
    app = FastAPI(title="AccessForge API", version="0.0.0")
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
        runners_router,
        runs_router,
        findings_router,
        exports_router,
    ):
        app.include_router(router)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        results = [
            check_database(config.database_url),
            check_evidence_store(config.evidence_endpoint_url),
        ]
        healthy = all(r.ok for r in results)
        return JSONResponse(
            status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "ready" if healthy else "not-ready",
                "dependencies": {r.name: {"ok": r.ok, "detail": r.detail} for r in results},
            },
        )

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {"config": config.redacted()}

    return app
