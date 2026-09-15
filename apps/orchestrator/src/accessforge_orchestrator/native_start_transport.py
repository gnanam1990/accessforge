"""One-shot private native host handoff, not a reader-start or completion attestation."""

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any
from uuid import UUID

from accessforge_persistence.supervisor_dispatch import DispatchTicket

from .manual_dispatch import DispatchReference, HandoffUnknown, ReaderTransportUnavailable

PROTOCOL = "accessforge.native-start.v1"


def _wire(reference: DispatchReference) -> dict[str, Any]:
    row: dict[str, Any] = {
        "workspaceId": reference.workspace_id,
        "runId": reference.run_id,
        "attemptId": reference.attempt_id,
        "runnerId": reference.runner_id,
        "leaseId": reference.lease_id,
        "epoch": reference.epoch,
    }
    if type(reference.epoch) is not int or not 1 <= reference.epoch <= 9007199254740991:
        raise ValueError("invalid native epoch")
    if any(str(UUID(value)) != value for key, value in row.items() if key != "epoch"):
        raise ValueError("canonical native identity required")
    return row


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate native acknowledgement field")
        result[key] = value
    return result


def _same_reference(value: Any, expected: dict[str, Any]) -> bool:
    # Python considers True == 1 and 1.0 == 1; neither is the wire integer epoch.
    return (
        type(value) is dict
        and value.keys() == expected.keys()
        and all(
            type(value[key]) is type(item) and value[key] == item for key, item in expected.items()
        )
    )


class NativeStartTransport:
    """Construct only from the trusted host's privateReference, never navigator/browser input.

    The host advertises this port only after its supported-profile gate. Each native execution
    independently validates the original dispatch ticket and current startup/action authority.
    This client grants none of those permissions and cannot be reused after a send attempt.
    """

    def __init__(self, private_reference: dict[str, Any], expected: DispatchReference) -> None:
        self._reference = _wire(expected)
        if (
            set(private_reference) != {"protocol", "socketPath", "token", "reference"}
            or private_reference["protocol"] != PROTOCOL
            or not _same_reference(private_reference["reference"], self._reference)
            or not isinstance(private_reference["socketPath"], str)
            or not isinstance(private_reference["token"], str)
            or len(private_reference["token"]) != 64
            or any(c not in "0123456789abcdef" for c in private_reference["token"])
        ):
            raise ReaderTransportUnavailable("private native handoff binding unavailable")
        self._path = Path(private_reference["socketPath"])
        self._token = private_reference["token"]
        self._consumed = False
        self._identity = self._inspect()

    def _inspect(self) -> tuple[int, int, int, int, int]:
        try:
            if not self._path.is_absolute() or self._path.resolve(strict=True) != self._path:
                raise ValueError
            parent, node = self._path.parent.lstat(), self._path.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or not stat.S_ISSOCK(node.st_mode)
                or parent.st_uid != os.getuid()
                or node.st_uid != os.getuid()
                or (parent.st_mode | node.st_mode) & 0o077
            ):
                raise ValueError
            return parent.st_dev, parent.st_ino, node.st_dev, node.st_ino, node.st_ctime_ns
        except (OSError, ValueError, AttributeError):
            raise ReaderTransportUnavailable(
                "private owned native handoff socket required"
            ) from None

    def check_available(self) -> None:
        if self._consumed or self._inspect() != self._identity:
            raise ReaderTransportUnavailable("native handoff consumed or changed; reconcile")

    async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
        self.check_available()
        if _wire(reference) != self._reference:
            raise ReaderTransportUnavailable("native handoff reference differs")
        self._consumed = True
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(5):
                reader, writer = await asyncio.open_unix_connection(str(self._path), limit=4096)
                if self._inspect() != self._identity:
                    raise ValueError("socket changed")
                packet = {
                    "protocol": PROTOCOL,
                    "token": self._token,
                    "envelope": {
                        "reference": self._reference,
                        "ticket": {
                            "ticketId": ticket.ticket_id,
                            "token": ticket.token,
                            "expiresAt": ticket.expires_at,
                        },
                    },
                }
                raw = json.dumps(packet, separators=(",", ":")).encode() + b"\n"
                if len(raw) > 4096:
                    raise ValueError("handoff too large")
                writer.write(raw)
                await writer.drain()
                response = await reader.readuntil(b"\n")
                if await reader.read(1) or self._inspect() != self._identity:
                    raise ValueError("ambiguous acknowledgement")
                received = json.loads(response, object_pairs_hook=_unique)
                if (
                    type(received) is not dict
                    or set(received) != {"protocol", "reference", "status"}
                    or received["protocol"] != PROTOCOL
                    or received["status"] != "HANDOFF_ACCEPTED"
                    or not _same_reference(received["reference"], self._reference)
                ):
                    raise ValueError("foreign acknowledgement")
        except asyncio.CancelledError:
            raise
        except Exception:
            raise HandoffUnknown("native handoff unconfirmed; reconcile original attempt") from None
        finally:
            if writer is not None:
                writer.close()
