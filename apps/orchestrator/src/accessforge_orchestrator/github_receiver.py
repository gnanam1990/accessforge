"""Separately hosted, exact-binding webhook receipts and deny-only repository removal.

This receiver has only a webhook secret and database access, never an App signing key/token.
It records authenticated bytes for the configured binding and can revoke explicit removed
repository access locally. It cannot approve work or claim current GitHub access.
No network publication or model calls occur.
"""

import asyncio
import json
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from accessforge_persistence import github_bindings, github_webhooks, workspace_connection

from .github_webhooks import MAX_WEBHOOK_BYTES, Refused, authenticate

BODY_READ_TIMEOUT_SECONDS = 5.0


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
    """Build, but do not start, one operator-configured receipt/deny-only ingress.

    Deploy separately from agent/build processes with TLS, connection/body-read deadlines,
    concurrency/rate limits and redacted server logs. This factory does not configure those
    host controls or provide multi-workspace routing. No JWT or private-key input exists.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/repository-removal")
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
        try:
            # One absolute deadline, not a fresh allowance for every trickled chunk.
            # No authentication/database work starts until the complete original body arrives.
            async with asyncio.timeout(BODY_READ_TIMEOUT_SECONDS):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_WEBHOOK_BYTES:
                        return JSONResponse({"status": "REFUSED"}, status_code=413)
                    raw.extend(chunk)
        except TimeoutError:
            return JSONResponse({"status": "REFUSED"}, status_code=408)
        try:
            removal = request.url.path == "/repository-removal"
            await run_in_threadpool(
                receive_repository_removal if removal else receive,
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
        return JSONResponse(
            {"status": "LOCAL_BINDING_REVOKED" if removal else "AUTHENTICATED_RECEIPT_ONLY"},
            status_code=202,
        )

    return app


def receive_repository_removal(
    config: ReceiverConfig, *, raw_body: bytes, signature: str, delivery_id: str
) -> github_webhooks.Receipt:
    """Authenticate explicit repository removal and atomically revoke only the configured binding.

    This dedicated deny-only ingress never uses the unsigned event header, grants access or
    resumes a revoked binding. Duplicate original bodies can acknowledge the same revocation.
    Empty removal lists (including selection-mode changes) require independent remote checks;
    they are not proof that every repository has been removed.
    """
    source = authenticate(
        raw_body, secret=config.webhook_secret, signature=signature, delivery_id=delivery_id
    )
    # authenticate already checked bounded UTF-8 JSON, duplicate keys and numeric identities.
    payload = json.loads(raw_body)
    removed = payload.get("repositories_removed")
    if (
        payload.get("action") != "removed"
        or type(payload["installation"].get("app_id")) is not int
        or payload["installation"]["app_id"] != config.app_id
        or not isinstance(removed, list)
        or not removed
        or any(
            not isinstance(item, dict)
            or type(item.get("id")) is not int
            or not 1 <= item["id"] <= 2**63 - 1
            for item in removed
        )
    ):
        raise Refused("explicit authenticated repository removal unavailable")
    removed_ids = {item["id"] for item in removed}
    with workspace_connection(config.database_url, config.workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        # Include already revoked rows for safe acknowledgement of original redeliveries.
        binding = conn.execute(
            "SELECT * FROM github_repository_binding WHERE id=%s AND workspace_id=%s FOR UPDATE",
            (config.binding_id, config.workspace_id),
        ).fetchone()
        if (
            binding is None
            or binding["app_id"] != config.app_id
            or binding["installation_id"] != source.installation_id
            or binding["repository_id"] not in removed_ids
            or source.repository_id not in (None, binding["repository_id"])
        ):
            raise Refused("repository removal differs from configured binding")
        receipt = github_webhooks.record_authenticated(
            conn,
            workspace_id=config.workspace_id,
            app_id=config.app_id,
            body_digest=source.body_digest,
            installation_id=source.installation_id,
            repository_id=source.repository_id,
            delivery_id=source.delivery_id,
        )
        github_bindings.disconnect(
            conn, workspace_id=config.workspace_id, binding_id=config.binding_id
        )
        return receipt
