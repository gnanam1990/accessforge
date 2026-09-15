"""Explicit independent collector process: private host input FD 3, receipts FD 4.

The host sends one bounded startup line, then FINISH referencing the committed READY event
and closes its input. FINISH is not STOP authority: the observer rechecks protected STOP.
Only event IDs leave this process; credentials, fixture nonce and measured effects do not.
"""

from __future__ import annotations

import json
import os
import select
import signal
import stat
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from .reference_effect_observer import ReferenceEffectObserver

PROTOCOL = "accessforge.reference-effect-observer.v1"
MAX_INPUT = 4096


class Interrupted(BaseException):
    """Interrupt blocking lifecycle work without being caught as an ordinary DB error."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate host key")
        result[key] = value
    return result


def _parse(data: bytes, keys: set[str]) -> dict[str, Any]:
    if not data or len(data) > MAX_INPUT or data[-1:] != b"\n" or b"\n" in data[:-1]:
        raise ValueError("one bounded private input line required")
    row = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
    if not isinstance(row, dict) or set(row) != keys or row.get("protocol") != PROTOCOL:
        raise ValueError("closed collector host protocol required")
    return row


def parse_start(data: bytes) -> dict[str, Any]:
    row = _parse(
        data,
        {
            "protocol",
            "workspaceId",
            "runId",
            "attemptId",
            "observerCredentialRef",
            "applicationRole",
            "installationId",
            "maxWallSeconds",
        },
    )
    for key in ("workspaceId", "runId", "attemptId", "installationId"):
        if not isinstance(row[key], str) or str(UUID(row[key])) != row[key]:
            raise ValueError("canonical collector identity required")
    for key, limit in (("observerCredentialRef", 256), ("applicationRole", 63)):
        value = row[key]
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("collector identity configuration unavailable")
    if type(row["maxWallSeconds"]) is not int or not 1 <= row["maxWallSeconds"] <= 1800:
        raise ValueError("collector wall time must be within 1..1800 seconds")
    return row


def _pipe(fd: int) -> None:
    mode = os.fstat(fd).st_mode
    if os.name != "posix" or not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise ValueError("private inherited pipe required")


def _read(fd: int, deadline: float, *, until_eof: bool) -> bytes:
    data = bytearray()
    while len(data) <= MAX_INPUT:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError("private input deadline elapsed")
        # Startup leaves the same pipe open for a later FINISH. Do not swallow a command
        # into a startup read buffer. Closure instead requires EOF and rejects extra records.
        chunk = os.read(fd, MAX_INPUT + 1 - len(data) if until_eof else 1)
        if not chunk:
            break
        data.extend(chunk)
        if not until_eof and chunk == b"\n":
            break
    if len(data) > MAX_INPUT:
        raise ValueError("private collector input exceeds limit")
    return bytes(data)


def _receipt(fd: int, config: dict[str, Any], status: str, event_id: str) -> None:
    if str(UUID(event_id)) != event_id:
        raise ValueError("committed collector receipt unavailable")
    payload = (
        json.dumps(
            {
                "protocol": PROTOCOL,
                "status": status,
                "workspaceId": config["workspaceId"],
                "runId": config["runId"],
                "attemptId": config["attemptId"],
                "eventId": event_id,
            },
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    # Receipts are below PIPE_BUF. Refuse an incomplete write, never acknowledge a replay.
    if os.write(fd, payload) != len(payload):
        raise ValueError("collector receipt write unconfirmed")


def run_private_host(
    input_fd: int,
    output_fd: int,
    environment: Mapping[str, str],
    arm_timeout: Callable[[int], None],
) -> None:
    """Own one process lifecycle; main supplies signal deadlines, tests use isolated pipes."""
    _pipe(input_fd)
    _pipe(output_fd)
    config = parse_start(_read(input_fd, time.monotonic() + 5, until_eof=False))
    product = environment.get("ACCESSFORGE_DATABASE_URL")
    application = environment.get("ACCESSFORGE_OBSERVER_DATABASE_URL")
    if not product or not application:
        raise ValueError("independent database configuration required")
    deadline = time.monotonic() + config["maxWallSeconds"]
    arm_timeout(config["maxWallSeconds"])
    observer = ReferenceEffectObserver(
        product,
        application,
        workspace_id=config["workspaceId"],
        run_id=config["runId"],
        credential_ref=config["observerCredentialRef"],
        application_role=config["applicationRole"],
        installation_id=config["installationId"],
        expected_attempt_id=config["attemptId"],
    )
    try:
        ready = observer.begin()  # Includes the actual READY transaction commit.
        if time.monotonic() >= deadline:
            raise ValueError("collector readiness expired")
        _receipt(output_fd, config, "READY_RETAINED", ready)
        command = _parse(
            _read(input_fd, deadline, until_eof=True), {"protocol", "command", "readyEventId"}
        )
        if command["command"] != "FINISH" or command["readyEventId"] != ready:
            raise ValueError("original collector ready receipt required")
        closed = observer.finish()  # Independently checks original successful STOP.
        if time.monotonic() >= deadline:
            raise ValueError("collector closure receipt expired")
        _receipt(output_fd, config, "CLOSED_RETAINED", closed)
    finally:
        observer.abort()


def main() -> int:
    if os.name != "posix" or sys.argv[1:] != ["--private-host"]:
        print(
            "usage: reference_effect_worker --private-host (inherited private pipes required)",
            file=sys.stderr,
        )
        return 64
    previous: dict[int, Any] = {}

    def interrupt(_signal: int, _frame: Any) -> None:
        raise Interrupted()

    def arm_timeout(seconds: int) -> None:
        signal.setitimer(signal.ITIMER_REAL, seconds)

    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            previous[number] = signal.signal(number, interrupt)
        signal.setitimer(signal.ITIMER_REAL, 5)
        run_private_host(3, 4, os.environ, arm_timeout)
        return 0
    except (Exception, Interrupted):
        print(
            "collector lifecycle unconfirmed; reconcile original records, do not retry",
            file=sys.stderr,
        )
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for saved_number, handler in previous.items():
            signal.signal(saved_number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
