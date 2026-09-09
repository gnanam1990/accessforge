"""Control-plane HTTP application."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from .config import ApiSettings
from .health import check_database, check_evidence_store


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    config = settings or ApiSettings()  # type: ignore[call-arg]
    app = FastAPI(title="AccessForge API", version="0.0.0")
    app.state.config = config

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
