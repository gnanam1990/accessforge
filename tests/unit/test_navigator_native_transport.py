"""Real local transport with synthetic supervisor responses. No reader or model is invoked."""

from __future__ import annotations

import json
import os
import socket
import tempfile
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import uuid4

import pytest

from accessforge_navigation_tools import SupervisorDispatchRequest
from accessforge_orchestrator.manual_dispatch import DispatchReference
from accessforge_orchestrator.navigator.native_transport import (
    PROTOCOL,
    NativeDispatchUnknown,
    NativeNavigatorTransport,
)


@pytest.mark.skipif(os.name != "posix", reason="private Unix transport requires POSIX")
@pytest.mark.parametrize("fault", [None, "foreign-reply", "lost-reply"])
async def test_native_client_transmits_only_fixture_ref_and_never_retries(
    fault: str | None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="afn-", dir="/tmp") as directory:
        path = Path(directory).resolve() / "a.sock"
        expected = DispatchReference(
            workspace_id=str(uuid4()),
            run_id=str(uuid4()),
            attempt_id=str(uuid4()),
            runner_id=str(uuid4()),
            lease_id=str(uuid4()),
            epoch=1,
        )
        reference = {
            "workspaceId": expected.workspace_id,
            "runId": expected.run_id,
            "attemptId": expected.attempt_id,
            "runnerId": expected.runner_id,
            "leaseId": expected.lease_id,
            "epoch": 1,
        }
        received: list[dict[str, Any]] = []
        failures: list[BaseException] = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen(1)
            listener.settimeout(5)

            def serve() -> None:
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(5)
                        data = bytearray()
                        while not data.endswith(b"\n"):
                            chunk = connection.recv(4096)
                            if not chunk:
                                raise ValueError("incomplete request")
                            data.extend(chunk)
                        request = json.loads(data)
                        received.append(request)
                        if fault == "lost-reply":
                            return
                        connection.sendall(
                            (
                                json.dumps(
                                    {
                                        "protocol": PROTOCOL,
                                        "reference": reference,
                                        "requestId": "foreign" if fault else request["requestId"],
                                        "sequence": request["sequence"],
                                        "status": "SUCCEEDED",
                                        "actionId": str(uuid4()),
                                    }
                                )
                                + "\n"
                            ).encode()
                        )
                except BaseException as exc:
                    failures.append(exc)

            worker = Thread(target=serve, daemon=True)
            worker.start()
            transport = NativeNavigatorTransport(
                private_reference={
                    "protocol": PROTOCOL,
                    "reference": reference,
                    "token": "a" * 64,
                    "socketPath": str(path),
                    "leaseRemainingMs": 10000,
                },
                expected=expected,
                run_ref="run",
            )
            request = SupervisorDispatchRequest(
                "run", "TYPE_TEXT", text="must-not-cross-port", text_value_ref="fullName"
            )
            if fault:
                with pytest.raises(NativeDispatchUnknown):
                    await transport(request)
                with pytest.raises(NativeDispatchUnknown):
                    await transport(request)
            else:
                result = await transport(request)
                assert result.status == "SUCCEEDED" and result.action_id is not None
            transport.close()
            worker.join(timeout=5)
            assert not worker.is_alive() and not failures
            assert len(received) == 1
            assert received[0]["command"] == {"action": "TYPE_TEXT", "textValueRef": "fullName"}
            assert "must-not-cross-port" not in json.dumps(received)
