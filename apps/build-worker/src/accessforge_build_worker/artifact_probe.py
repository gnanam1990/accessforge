"""Private same-host read-only measurement bridge for the trusted desktop controller.

The capability never belongs in browser JSON, navigator context, logs or evidence. It can only
invoke the bound gateway's live measurement (and its server-owned receipt callback), not input.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import socket
import stat
import tempfile
import threading
from pathlib import Path
from types import TracebackType
from typing import Any

from .candidate_gateway import CandidateGateway
from .sandbox import CleanupUnconfirmed, SandboxRefused

PROTOCOL = "accessforge.artifact-probe.v1"
_LOGGER = logging.getLogger(__name__)


class ArtifactProbe:
    def __init__(self, gateway: CandidateGateway, *, private_directory: Path) -> None:
        self._gateway = gateway
        self._parent = private_directory
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._socket: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._token = secrets.token_hex(32)
        self._path: Path | None = None
        self._started = False

    def __enter__(self) -> ArtifactProbe:
        if self._started:
            raise SandboxRefused("artifact probe cannot be reopened")
        self._started = True
        _ = self._gateway.origin
        parent = self._parent.resolve(strict=True)
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SandboxRefused("artifact probe requires an owned private host directory")
        self._directory = tempfile.TemporaryDirectory(prefix="probe-", dir=parent)
        self._path = Path(self._directory.name) / "read.sock"
        try:
            if len(os.fsencode(self._path)) > 100:
                raise SandboxRefused("artifact probe socket path too long")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket = listener
            listener.bind(str(self._path))
            self._path.chmod(0o600)
            listener.listen(1)
            listener.settimeout(0.2)
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()
        except BaseException:
            self.close()
            raise
        return self

    def reference(self) -> dict[str, Any]:
        """Private controller-to-host handoff only; this is not execution authorization."""
        if (
            self._path is None
            or self._stop.is_set()
            or self._thread is None
            or not self._thread.is_alive()
        ):
            raise SandboxRefused("artifact probe is not live")
        _ = self._gateway.origin
        binding = self._gateway.binding
        return {
            "protocol": PROTOCOL,
            "socketPath": str(self._path),
            "token": self._token,
            "taskId": binding.task_id,
            "candidateId": binding.candidate_id,
            "imageId": binding.image_id,
            "daemonId": binding.daemon.daemon_id,
        }

    def _serve(self) -> None:
        assert self._socket is not None
        requests = 0
        while requests < 128:
            if self._stop.is_set():
                return
            try:
                connection, _address = self._socket.accept()
            except TimeoutError:
                # Idle polling must not consume the request budget.
                continue
            except OSError:
                return
            requests += 1
            self._connection = connection
            try:
                with connection:
                    connection.settimeout(1)
                    data = bytearray()
                    while b"\n" not in data and len(data) <= 1024:
                        chunk = connection.recv(1025 - len(data))
                        if not chunk:
                            raise SandboxRefused("incomplete probe request")
                        data.extend(chunk)
                    if len(data) > 1024 or not data.endswith(b"\n") or data.count(b"\n") != 1:
                        raise SandboxRefused("bounded probe request required")
                    request = json.loads(data)
                    if (
                        not isinstance(request, dict)
                        or set(request) != {"protocol", "requestId", "token"}
                        or request["protocol"] != PROTOCOL
                        or not isinstance(request["token"], str)
                        or not hmac.compare_digest(request["token"], self._token)
                        or not isinstance(request["requestId"], str)
                        or not re.fullmatch(r"[a-f0-9]{32}", request["requestId"])
                        or self._stop.is_set()
                    ):
                        raise SandboxRefused("probe capability unavailable")
                    observation = self._gateway.observe_artifact()
                    if self._stop.is_set():
                        return
                    response = (
                        json.dumps(
                            {
                                "protocol": PROTOCOL,
                                "requestId": request["requestId"],
                                "observation": observation,
                            }
                        ).encode()
                        + b"\n"
                    )
                    if len(response) > 8192:
                        raise SandboxRefused("probe response too large")
                    connection.sendall(response)
            except Exception:
                # No raw exception, token, candidate path or database information crosses the port.
                # A missing response is unavailable, never cached evidence or a replay instruction.
                _LOGGER.debug("private artifact probe request unavailable")
            finally:
                self._connection = None
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=6)
            if self._thread.is_alive():
                raise CleanupUnconfirmed(
                    "artifact measurement did not stop; do not reuse the candidate"
                )
        if self._directory is not None:
            self._directory.cleanup()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
