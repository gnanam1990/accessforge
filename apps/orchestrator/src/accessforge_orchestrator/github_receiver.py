"""Separately hosted, exact-binding webhook authentication receipt endpoint.

This receiver has only a webhook secret and database access, never an App signing key/token.
It records authenticated bytes for the configured live binding; it does not dispatch events,
approve work, or claim current GitHub access. No network publication or model calls occur.
"""

from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from accessforge_persistence import github_bindings, github_webhooks, workspace_connection

from .github_webhooks import MAX_WEBHOOK_BYTES, Refused, authenticate


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    workspace_id: str
    binding_id: str
    app_id: int
    database_url: str = field(repr=False)
    webhook_secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        try:
            for value in (self.workspace_id, self.binding_id):
                if not isinstance(value, str) or str(UUID(value)) != value:
                    raise ValueError
            if type(self.app_id) is not int or not 1 <= self.app_id <= 2**63 - 1:
                raise ValueError
            if type(self.webhook_secret) is not bytes or not 32 <= len(self.webhook_secret) <= 4096:
                raise ValueError
            if not isinstance(self.database_url, str) or not self.database_url:
                raise ValueError
        except ValueError:
            raise Refused("trusted receiver configuration unavailable") from None


def receive(
    config: ReceiverConfig, *, raw_body: bytes, signature: str, delivery_id: str
) -> github_webhooks.Receipt:
    """Authenticate before database access, then lock/recheck exact local scope and dedup.

    Scope comes from operator configuration, never workspace/event headers. The receipt is
    not event consumption: current remote access and event-specific handling remain pending.
    Binding revocation and receipt insertion serialize in one short database transaction.
    """
    source = authenticate(
        raw_body, secret=config.webhook_secret, signature=signature, delivery_id=delivery_id
    )
    with workspace_connection(config.database_url, config.workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        binding = github_bindings.require_live(
            conn, workspace_id=config.workspace_id, binding_id=config.binding_id
        )
        if (
            binding["app_id"] != config.app_id
            or binding["installation_id"] != source.installation_id
            or binding["repository_id"] != source.repository_id
        ):
            raise Refused("authenticated webhook scope differs from receiver binding")
        return github_webhooks.record_authenticated(
            conn,
            workspace_id=config.workspace_id,
            app_id=config.app_id,
            body_digest=source.body_digest,
            installation_id=source.installation_id,
            repository_id=source.repository_id,
            delivery_id=source.delivery_id,
        )


def create_receiver(config: ReceiverConfig) -> FastAPI:
    """Build, but do not start, one operator-configured receipt-only ingress.

    Deploy separately from agent/build processes with TLS, connection/body-read deadlines,
    concurrency/rate limits and redacted server logs. This factory does not configure those
    host controls or provide multi-workspace routing. No JWT or private-key input exists.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/webhook")
    async def webhook(request: Request) -> JSONResponse:
        # These headers are metadata, not body-HMAC authority. Refuse ambiguity instead of
        # relying on whichever duplicate value a proxy/library happens to select.
        signature = request.headers.getlist("x-hub-signature-256")
        delivery = request.headers.getlist("x-github-delivery")
        if len(signature) != 1 or len(delivery) != 1:
            return JSONResponse({"status": "REFUSED"}, status_code=400)
        encoding = request.headers.getlist("content-encoding")
        if encoding and encoding != ["identity"]:
            return JSONResponse({"status": "REFUSED"}, status_code=415)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_WEBHOOK_BYTES:
                return JSONResponse({"status": "REFUSED"}, status_code=413)
            raw.extend(chunk)
        try:
            await run_in_threadpool(
                receive,
                config,
                raw_body=bytes(raw),
                signature=signature[0],
                delivery_id=delivery[0],
            )
        except (Refused, github_bindings.Refused, github_webhooks.Refused):
            return JSONResponse({"status": "REFUSED"}, status_code=403)
        except psycopg.Error:
            # A lost commit response is uncertain. Re-delivery is safe only because receipt
            # storage has durable body+alias deduplication; this does not retry remote actions.
            return JSONResponse({"status": "UNCONFIRMED"}, status_code=503)
        # Same response on first delivery/replay, and no tenant/binding/event content returned.
        return JSONResponse({"status": "AUTHENTICATED_RECEIPT_ONLY"}, status_code=202)

    return app
