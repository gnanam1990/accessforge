"""HTTP surface for the reference application."""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from . import db, templates
from .config import ReferenceAppSettings
from .validation import validate_service_request


def _settings() -> ReferenceAppSettings:
    return ReferenceAppSettings()  # type: ignore[call-arg]


def create_app(settings: ReferenceAppSettings | None = None) -> FastAPI:
    config = settings or _settings()
    app = FastAPI(title="AccessForge reference application", docs_url=None, redoc_url=None)
    app.state.config = config

    def require_setup(x_setup_token: Annotated[str | None, Header()] = None) -> None:
        if not x_setup_token or not secrets.compare_digest(x_setup_token, config.setup_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "setup identity required")

    def require_observer(x_observer_token: Annotated[str | None, Header()] = None) -> None:
        if not x_observer_token or not secrets.compare_digest(
            x_observer_token, config.observer_token
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "observer identity required")

    # ---- health -------------------------------------------------------------------------
    # Liveness and readiness are different questions. A process that responds to HTTP is alive;
    # it is only ready if the state it depends on is actually reachable.

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> Response:
        try:
            db.ping(config.database_url)
        except psycopg.Error as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not-ready",
                    "dependency": "postgresql",
                    # Class name only: a driver message can carry the connection string.
                    "reason": type(exc).__name__,
                },
            )
        return JSONResponse(content={"status": "ready", "dependencies": {"postgresql": "ok"}})

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return {"config": config.redacted()}

    # ---- fixture lifecycle (setup identity only) ----------------------------------------

    @app.post(
        "/api/_test/fixtures",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_setup)],
    )
    def create_fixture(variant: str) -> dict[str, str]:
        if variant not in ("accessible", "inaccessible"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown variant")
        nonce = secrets.token_urlsafe(16)
        digest = templates.template_digest(variant)
        with db.transaction(config.database_url) as conn:
            conn.execute(
                "INSERT INTO fixture_instance (nonce, template_digest, variant) VALUES (%s,%s,%s)",
                (nonce, digest, variant),
            )
        return {"nonce": nonce, "variant": variant, "template_digest": digest}

    @app.post(
        "/api/_test/reset",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_setup)],
    )
    def reset() -> Response:
        # Scoped to this application's own tables. It does not drop schemas or touch anything it
        # does not own.
        with db.transaction(config.database_url) as conn:
            conn.execute("TRUNCATE service_request, fixture_instance CASCADE")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/_test/receipt/{nonce}", dependencies=[Depends(require_observer)])
    def receipt(nonce: str) -> dict[str, Any]:
        """The observer's independent read of durable state.

        Reachable only with the observer identity. The navigator must never hold this token: it
        would hand the navigator the answer key it is supposed to establish by observation.
        """
        with db.transaction(config.database_url) as conn:
            rows = conn.execute(
                "SELECT id, full_name, email, category, description, created_at "
                "FROM service_request WHERE fixture_nonce = %s",
                (nonce,),
            ).fetchall()
        return {
            "fixture_nonce": nonce,
            "request_count": len(rows),
            "requests": [
                {
                    "id": str(r["id"]),
                    "full_name": r["full_name"],
                    "email": r["email"],
                    "category": r["category"],
                    "created_at": r["created_at"].isoformat(),
                }
                for r in rows
            ],
        }

    # ---- the journey surface -------------------------------------------------------------

    def _load_variant(nonce: str) -> str:
        with db.transaction(config.database_url) as conn:
            row = conn.execute(
                "SELECT variant FROM fixture_instance WHERE nonce = %s", (nonce,)
            ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown fixture instance")
        return str(row["variant"])

    @app.get("/form/{nonce}", response_class=HTMLResponse)
    def show_form(nonce: str) -> HTMLResponse:
        return HTMLResponse(templates.render_form(nonce=nonce, variant=_load_variant(nonce)))

    @app.post("/form/{nonce}", response_class=HTMLResponse)
    def submit_form(
        nonce: str,
        full_name: Annotated[str, Form()] = "",
        email: Annotated[str, Form()] = "",
        category: Annotated[str, Form()] = "",
        description: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        variant = _load_variant(nonce)
        values = {
            "full_name": full_name,
            "email": email,
            "category": category,
            "description": description,
        }
        errors = validate_service_request(**values)
        if errors:
            return HTMLResponse(
                templates.render_form(nonce=nonce, variant=variant, values=values, errors=errors),
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        request_id = uuid.uuid4()
        try:
            with db.transaction(config.database_url) as conn:
                conn.execute(
                    "INSERT INTO service_request "
                    "(id, fixture_nonce, full_name, email, category, description) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        request_id,
                        nonce,
                        full_name.strip(),
                        email.strip(),
                        category,
                        description.strip(),
                    ),
                )
        except psycopg.errors.UniqueViolation:
            # The one-request-per-fixture rule is enforced in the database, so a resubmission is
            # a visible conflict rather than a second silent receipt.
            raise HTTPException(
                status.HTTP_409_CONFLICT, "this fixture instance already has a request"
            ) from None

        return HTMLResponse(
            templates.render_form(nonce=nonce, variant=variant, receipt_id=str(request_id)),
            status_code=status.HTTP_201_CREATED,
        )

    return app
