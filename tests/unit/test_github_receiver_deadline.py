"""Ingress resource boundary only; no database, GitHub or installation authority proof."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from accessforge_orchestrator import github_receiver as receiver


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", [True, False])
@pytest.mark.parametrize("mounted", [True, False])
@pytest.mark.parametrize(
    "path,handler",
    [
        ("/webhook", "receive"),
        ("/repository-removal", "receive_repository_removal"),
        ("/installation-suspension", "receive_installation_suspension"),
    ],
)
async def test_body_deadline_precedes_receipt_work(
    monkeypatch: pytest.MonkeyPatch, stalled: bool, mounted: bool, path: str, handler: str
) -> None:
    calls: list[bytes] = []

    def receive(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs["raw_body"])

    async def body() -> AsyncIterator[bytes]:
        yield b'{"installation":'
        if stalled:
            await asyncio.Event().wait()
        yield b'{"id":1}}'

    monkeypatch.setattr(receiver, "BODY_READ_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(receiver, handler, receive)
    app = receiver.create_receiver(
        receiver.ReceiverConfig(
            workspace_id="00000000-0000-4000-8000-000000000001",
            binding_id="00000000-0000-4000-8000-000000000002",
            app_id=1,
            database_url="unused",
            webhook_secret=b"x" * 32,
        )
    )
    if mounted:
        parent = FastAPI()
        parent.mount("/integration", app)
        app = parent
        path = "/integration" + path
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        async with asyncio.timeout(1):
            response = await client.post(
                path,
                content=body(),
                headers={
                    "x-hub-signature-256": "sha256=" + "0" * 64,
                    "x-github-delivery": "00000000-0000-4000-8000-000000000003",
                },
            )
    if stalled:
        assert response.status_code == 408
        assert response.json() == {"status": "REFUSED"}
        assert calls == []
    else:
        assert response.status_code == 202
        assert response.json() == {
            "status": "AUTHENTICATED_RECEIPT_ONLY"
            if handler == "receive"
            else "LOCAL_BINDING_REVOKED"
        }
        assert calls == [b'{"installation":{"id":1}}']
