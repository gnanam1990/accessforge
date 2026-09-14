"""Real loopback TCP, synthetic fixture replies; no model, reader or application DB."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from accessforge_contracts.reference_fixture import (
    REFERENCE_FIXTURE_DIGEST,
    REFERENCE_FIXTURE_VERSION,
)
from accessforge_orchestrator import reference_fixture_setup as setup


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["created", "replayed", "redirect", "oversized", "slow-header"])
async def test_setup_transport_is_single_bounded_request(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[bytes] = []
    finished = asyncio.Event()
    monkeypatch.setattr(setup, "_SETUP_DEADLINE_SECONDS", 0.5)
    # An environment proxy must not receive the setup credential.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            received.append(await reader.readuntil(b"\r\n\r\n"))
            if case == "slow-header":
                writer.write(b"HTTP/1.1 201 Created\r\nX-Slow: ")
                await writer.drain()
                # Reads wait for the client's cancellation to close the connection.
                assert await reader.read() == b""
                return
            body = json.dumps(
                {
                    "nonce": "reserved-nonce-1234",
                    "variant": "inaccessible",
                    "template_digest": REFERENCE_FIXTURE_DIGEST,
                    "template_version": REFERENCE_FIXTURE_VERSION,
                }
            ).encode()
            status = 200 if case == "replayed" else 201
            if case == "oversized":
                body = b"x" * 16385
            if case == "redirect":
                status = 307
            writer.write(
                f"HTTP/1.1 {status} Reply\r\nContent-Length: {len(body)}\r\n"
                "Location: http://127.0.0.1:1/credential-sink\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        context: dict[str, Any] = {
            "origin": f"http://127.0.0.1:{port}",
            "nonce": "reserved-nonce-1234",
            "variant": "inaccessible",
        }
        async with asyncio.timeout(3):
            if case == "slow-header":
                with pytest.raises(TimeoutError):
                    await setup._provision_async(context, "synthetic-setup-token")
            elif case in {"redirect", "oversized"}:
                with pytest.raises(setup.Refused):
                    await setup._provision_async(context, "synthetic-setup-token")
            else:
                assert await setup._provision_async(context, "synthetic-setup-token") == (
                    200 if case == "replayed" else 201
                )
            await finished.wait()
    assert len(received) == 1
    assert (
        b"POST /api/_test/fixtures?variant=inaccessible&nonce=reserved-nonce-1234 " in received[0]
    )
    assert b"x-setup-token: synthetic-setup-token" in received[0]
