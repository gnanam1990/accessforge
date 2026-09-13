"""Private same-host action transport; no reader startup, provider call or retry authority."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import stat
import time
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from accessforge_navigation_tools import (
    ActionName,
    DispatchResult,
    ProposedAction,
    SupervisorDispatchRequest,
)
from accessforge_orchestrator.manual_dispatch import DispatchReference

PROTOCOL = "accessforge.navigator-action.v1"


class NativeDispatchUnknown(RuntimeError):
    """The action may have occurred. Reconcile canonical state; never retry the client."""


class NativeNavigatorTransport:
    def __init__(
        self, *, private_reference: dict[str, Any], expected: DispatchReference, run_ref: str
    ) -> None:
        reference = {
            "workspaceId": expected.workspace_id,
            "runId": expected.run_id,
            "attemptId": expected.attempt_id,
            "runnerId": expected.runner_id,
            "leaseId": expected.lease_id,
            "epoch": expected.epoch,
        }
        for key, value in reference.items():
            if key != "epoch" and str(UUID(str(value))) != value:
                raise ValueError("canonical native dispatch identity required")
        if (
            type(expected.epoch) is not int
            or expected.epoch < 1
            or not run_ref
            or set(private_reference)
            != {"protocol", "socketPath", "token", "reference", "leaseRemainingMs"}
            or private_reference["protocol"] != PROTOCOL
            or private_reference["reference"] != reference
            or not isinstance(private_reference["token"], str)
            or len(private_reference["token"]) != 64
            or any(char not in "0123456789abcdef" for char in private_reference["token"])
            or not isinstance(private_reference["socketPath"], str)
            or type(private_reference["leaseRemainingMs"]) is not int
            or not 1 <= private_reference["leaseRemainingMs"] <= 1800000
        ):
            raise ValueError("private native dispatch binding unavailable")
        self._reference = reference
        self._path = Path(private_reference["socketPath"])
        self._token = private_reference["token"]
        self._run_ref = run_ref
        # The native host's monotonic lease remains authoritative. This local upper bound never
        # resets between actions and does not assume that an action's several phases total 30s.
        # Delayed capability delivery cannot extend native authority: the server still expires.
        self._deadline = time.monotonic() + private_reference["leaseRemainingMs"] / 1000
        self._sequence = 0
        self._busy = self._fenced = False
        self._connection: socket.socket | None = None
        self._identity = self._check_path()

    def _check_path(self) -> tuple[int, int, int, int, int]:
        if not self._path.is_absolute() or self._path.resolve(strict=True) != self._path:
            raise ValueError("native socket path unavailable")
        parent, info = self._path.parent.lstat(), self._path.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or not stat.S_ISSOCK(info.st_mode)
            or parent.st_uid != os.getuid()
            or info.st_uid != os.getuid()
            or parent.st_mode & 0o077
            or info.st_mode & 0o077
        ):
            raise ValueError("private owned native socket required")
        return parent.st_dev, parent.st_ino, info.st_dev, info.st_ino, info.st_ctime_ns

    def close(self) -> None:
        self._fenced = True
        connection = self._connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    async def __call__(self, request: SupervisorDispatchRequest) -> DispatchResult:
        if self._fenced or self._busy:
            self.close()
            raise NativeDispatchUnknown("native dispatch fenced; no retry")
        self._busy = True
        try:
            if request.run_ref != self._run_ref:
                raise ValueError("native run reference differs")
            proposal = ProposedAction.model_validate(
                {
                    "run_ref": request.run_ref,
                    "action": ActionName(request.action),
                    "key_chord": request.key_chord,
                    "text_value_ref": request.text_value_ref,
                }
            )
            command = {"action": proposal.action.value}
            if proposal.text_value_ref is not None:
                command["textValueRef"] = proposal.text_value_ref
            if proposal.key_chord is not None:
                command["keyChord"] = proposal.key_chord
            # request.text is deliberately never transmitted. Only the native supervisor may
            # resolve a fixture reference into text under its own current sealed policy.
            self._sequence += 1
            result = await asyncio.to_thread(self._send, command, self._sequence)
            if result.status in {"AMBIGUOUS", "REFUSED"} or request.action == "STOP":
                self.close()
            return result
        except BaseException as exc:
            self.close()
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise NativeDispatchUnknown(
                "native action delivery unconfirmed; reconcile, never retry"
            ) from None
        finally:
            self._busy = False

    def _send(self, command: dict[str, str], sequence: int) -> DispatchResult:
        request_id = secrets.token_hex(16)
        payload = (
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "token": self._token,
                    "reference": self._reference,
                    "requestId": request_id,
                    "sequence": sequence,
                    "command": command,
                }
            )
            + "\n"
        ).encode()
        if len(payload) > 4096 or self._fenced or self._check_path() != self._identity:
            raise ValueError("native request unavailable")
        deadline = self._deadline
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            self._connection = connection
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("native lease budget elapsed before connect")
                connection.settimeout(remaining)
                if self._fenced:
                    raise ValueError("cancelled before connect")
                connection.connect(str(self._path))
                if self._fenced or self._check_path() != self._identity:
                    raise ValueError("cancelled or replaced before send")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("native lease budget elapsed before send")
                connection.settimeout(remaining)
                connection.sendall(payload)
                response = bytearray()
                while len(response) <= 8192:
                    remaining = deadline - time.monotonic()
                    if self._fenced or remaining <= 0:
                        raise TimeoutError("native action deadline elapsed")
                    connection.settimeout(remaining)
                    chunk = connection.recv(8193 - len(response))
                    if not chunk:
                        break
                    response.extend(chunk)
            finally:
                self._connection = None
        if (
            self._fenced
            or self._check_path() != self._identity
            or len(response) > 8192
            or not response.endswith(b"\n")
            or response.count(b"\n") != 1
        ):
            raise ValueError("native action response unavailable")
        value = json.loads(response)
        if (
            not isinstance(value, dict)
            or set(value)
            != {"protocol", "reference", "requestId", "sequence", "status", "actionId"}
            or value["protocol"] != PROTOCOL
            or value["reference"] != self._reference
            or value["requestId"] != request_id
            or type(value["sequence"]) is not int
            or value["sequence"] != sequence
            or value["status"] not in {"SUCCEEDED", "FAILED", "AMBIGUOUS", "REFUSED"}
            or (value["actionId"] is None and value["status"] != "REFUSED")
        ):
            raise ValueError("native response binding differs")
        if value["actionId"] is not None and str(UUID(value["actionId"])) != value["actionId"]:
            raise ValueError("native result has no canonical action identity")
        return DispatchResult(
            cast(Literal["SUCCEEDED", "FAILED", "AMBIGUOUS", "REFUSED"], value["status"]),
            action_id=value["actionId"],
            detail="Authenticated supervisor result; not task completion",
        )
