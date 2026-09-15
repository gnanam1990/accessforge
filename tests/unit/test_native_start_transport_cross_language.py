"""Real TS listener/Python client; qualification and native execution are synthetic."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from accessforge_orchestrator.manual_dispatch import DispatchReference, ReaderTransportUnavailable
from accessforge_orchestrator.native_start_transport import NativeStartTransport
from accessforge_persistence.supervisor_dispatch import DispatchTicket


def test_python_dispatch_reaches_typescript_listener() -> None:
    async def run() -> None:
        node = shutil.which("node")
        assert node is not None, "Node is required for the real cross-language handoff check"
        root = Path(__file__).resolve().parents[2]
        reference = DispatchReference(
            workspace_id=str(uuid4()),
            run_id=str(uuid4()),
            attempt_id=str(uuid4()),
            runner_id=str(uuid4()),
            lease_id=str(uuid4()),
            epoch=1,
        )
        wire = {
            "workspaceId": reference.workspace_id,
            "runId": reference.run_id,
            "attemptId": reference.attempt_id,
            "runnerId": reference.runner_id,
            "leaseId": reference.lease_id,
            "epoch": reference.epoch,
        }
        ticket = DispatchTicket(str(uuid4()), "2099-01-01T00:00:00Z", "t" * 43)
        with tempfile.TemporaryDirectory(prefix="af-wire-", dir="/tmp") as directory:
            process = await asyncio.create_subprocess_exec(
                node,
                "--experimental-test-module-mocks",
                str(root / "tests/fixtures/native-start-peer.mjs"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdin is not None and process.stdout is not None
            try:
                async with asyncio.timeout(10):
                    process.stdin.write(
                        (
                            json.dumps(
                                {
                                    "directory": str(Path(directory).resolve()),
                                    "reference": wire,
                                    "ticket": {
                                        "ticketId": ticket.ticket_id,
                                        "token": ticket.token,
                                        "expiresAt": ticket.expires_at,
                                    },
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    await process.stdin.drain()
                    private = json.loads(await process.stdout.readline())
                    transport = NativeStartTransport(private, reference)
                    await transport.start(reference, ticket=ticket)
                    with pytest.raises(ReaderTransportUnavailable):
                        await transport.start(reference, ticket=ticket)
                    process.stdin.write(b"finish\n")
                    await process.stdin.drain()
                    stdout, stderr = await process.communicate()
                    assert process.returncode == 0, stderr.decode()
                    assert stdout == b"SYNTHETIC_EXECUTION_CLOSED\n"
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    asyncio.run(run())
