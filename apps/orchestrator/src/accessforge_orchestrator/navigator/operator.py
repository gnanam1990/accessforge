"""Explicit POSIX child entrypoint; private host input on FD 3, bounded disposition on FD 4.

No secret-bearing configuration or model output is put in command arguments or the result pipe.
The native parent owns startup, deadline, independent observer closure and explicit finish.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import select
import signal
import stat
import time
from typing import Any
from uuid import UUID

from accessforge_domain.navigator_model import validate_profile
from accessforge_orchestrator.manual_dispatch import DispatchReference

from .config import NavigatorModelProfile
from .coordinator import NativeNavigatorSession

MAX_INPUT = 16384


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate host input")
        result[key] = value
    return result


def _pipe(fd: int) -> None:
    mode = os.fstat(fd).st_mode
    if os.name != "posix" or not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise ValueError("private inherited pipe required")


def read_host_input() -> dict[str, Any]:
    _pipe(3)
    deadline = time.monotonic() + 5
    data = bytearray()
    try:
        while len(data) <= MAX_INPUT:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([3], [], [], remaining)[0]:
                raise ValueError("host input deadline elapsed")
            chunk = os.read(3, MAX_INPUT + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if not data or len(data) > MAX_INPUT:
            raise ValueError("bounded host envelope required")
    finally:
        os.close(3)
    return parse_host_input(bytes(data))


def parse_host_input(data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_INPUT:
        raise ValueError("bounded host envelope required")
    config = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(config, dict)
        or set(config)
        != {
            "schemaVersion",
            "reference",
            "consentId",
            "modelProfile",
            "privateReference",
        }
        or type(config["schemaVersion"]) is not int
        or config["schemaVersion"] != 1
    ):
        raise ValueError("closed host envelope required")
    reference = config["reference"]
    if not isinstance(reference, dict) or set(reference) != {
        "workspaceId",
        "runId",
        "attemptId",
        "runnerId",
        "leaseId",
        "epoch",
    }:
        raise ValueError("exact dispatch reference required")
    for key, value in reference.items():
        if key == "epoch":
            if type(value) is not int or not 1 <= value <= 2**53 - 1:
                raise ValueError("bounded lease epoch required")
        elif not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("canonical dispatch identity required")
    if (
        not isinstance(config["consentId"], str)
        or str(UUID(config["consentId"])) != config["consentId"]
    ):
        raise ValueError("canonical consent identity required")
    if not isinstance(config["modelProfile"], dict) or not isinstance(
        config["privateReference"], dict
    ):
        raise ValueError("exact model and native capability required")
    validate_profile(config["modelProfile"])
    return config


async def run_host_session(config: dict[str, Any], database_url: str) -> dict[str, Any]:
    raw = config["reference"]
    reference = DispatchReference(
        workspace_id=raw["workspaceId"],
        run_id=raw["runId"],
        attempt_id=raw["attemptId"],
        runner_id=raw["runnerId"],
        lease_id=raw["leaseId"],
        epoch=raw["epoch"],
    )
    session = NativeNavigatorSession(
        database_url=database_url,
        reference=reference,
        consent_id=config["consentId"],
        profile=NavigatorModelProfile.model_validate(config["modelProfile"]),
        private_reference=config["privateReference"],
    )
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()

    def cancel() -> None:
        session.close()
        if task is not None:
            task.cancel()

    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)}
    try:
        for kind in previous:
            loop.add_signal_handler(kind, cancel)
        for count in range(1, 501):
            result = await session.run_next_turn()
            if result.next_action_sequence is None:
                return {
                    "schemaVersion": 1,
                    "reference": raw,
                    "status": "STOP_ACKNOWLEDGED" if result.stop_acknowledged else "NOT_FINISHED",
                    "completedCalls": count,
                    "lastOperationId": result.operation_id,
                }
        raise ValueError("maximum navigator calls consumed without STOP")
    finally:
        session.close()
        for kind, handler in previous.items():
            loop.remove_signal_handler(kind)
            signal.signal(kind, handler)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-billable-model-call", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_billable_model_call:
        parser.error("explicit --allow-billable-model-call required; no provider work started")
    try:
        _pipe(4)
        config = read_host_input()
        result = asyncio.run(run_host_session(config, os.environ["ACCESSFORGE_DATABASE_URL"]))
        payload = (json.dumps(result, separators=(",", ":")) + "\n").encode()
        if len(payload) > 2048 or os.write(4, payload) != len(payload):
            raise ValueError("result delivery unknown")
    except (Exception, asyncio.CancelledError):
        # Native parent discards stdout/stderr and treats missing/truncated/failed receipts as
        # unknown. Never serialize exception text, SDK output, database URLs or native tokens.
        parser.exit(1, "NAVIGATOR_UNCONFIRMED; inspect original invocation; do not replay.\n")
    parser.exit(0 if result["status"] == "STOP_ACKNOWLEDGED" else 1)


if __name__ == "__main__":
    main()
