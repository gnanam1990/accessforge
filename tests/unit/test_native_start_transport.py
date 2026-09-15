"""Real Unix socket checks with a synthetic host; no reader/model execution proof."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from accessforge_orchestrator.manual_dispatch import (
    DispatchReference,
    HandoffUnknown,
    ReaderTransportUnavailable,
)
from accessforge_orchestrator.native_start_transport import PROTOCOL, NativeStartTransport
from accessforge_persistence.supervisor_dispatch import DispatchTicket


@pytest.mark.parametrize("mode", ["valid", "foreign", "duplicate", "extra", "oversize", "cancel"])
def test_one_shot_handoff(mode):
    async def run():
        reference = DispatchReference(*(str(uuid4()) for _ in range(5)), epoch=1)
        wire = dict(
            zip(
                ("workspaceId", "runId", "attemptId", "runnerId", "leaseId", "epoch"),
                (
                    reference.workspace_id,
                    reference.run_id,
                    reference.attempt_id,
                    reference.runner_id,
                    reference.lease_id,
                    1,
                ),
                strict=True,
            )
        )
        ticket = DispatchTicket(str(uuid4()), "2099-01-01T00:00:00Z", "t" * 43)
        received = []
        delivered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def peer(reader, writer):
            try:
                received.append(json.loads(await reader.readline()))
                delivered.set()
                if mode == "cancel":
                    await release.wait()
                    return
                ack = {"protocol": PROTOCOL, "reference": wire, "status": "HANDOFF_ACCEPTED"}
                if mode == "foreign":
                    ack["reference"] = {**wire, "epoch": 2}
                raw = json.dumps(ack)
                if mode == "duplicate":
                    raw = raw[:-1] + ', "status":"HANDOFF_ACCEPTED"}'
                if mode == "oversize":
                    raw = "x" * 5000
                writer.write((raw + "\n" + ("extra" if mode == "extra" else "")).encode())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        with tempfile.TemporaryDirectory(prefix="af-start-", dir="/tmp") as directory:
            path = Path(directory).resolve() / "s.sock"
            server = await asyncio.start_unix_server(peer, path=str(path))
            os.chmod(path, 0o600)
            try:
                transport = NativeStartTransport(
                    {
                        "protocol": PROTOCOL,
                        "socketPath": str(path),
                        "token": "a" * 64,
                        "reference": wire,
                    },
                    reference,
                )
                task = asyncio.create_task(transport.start(reference, ticket=ticket))
                if mode == "cancel":
                    await asyncio.wait_for(delivered.wait(), 2)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    release.set()
                elif mode == "valid":
                    await task
                else:
                    with pytest.raises(HandoffUnknown, match="reconcile"):
                        await task
                with pytest.raises(ReaderTransportUnavailable, match="consumed"):
                    transport.check_available()
                assert received == [
                    {
                        "protocol": PROTOCOL,
                        "token": "a" * 64,
                        "envelope": {
                            "reference": wire,
                            "ticket": {
                                "ticketId": ticket.ticket_id,
                                "token": ticket.token,
                                "expiresAt": ticket.expires_at,
                            },
                        },
                    }
                ]
                await asyncio.wait_for(finished.wait(), 2)
            finally:
                release.set()
                server.close()
                await server.wait_closed()

    asyncio.run(run())
