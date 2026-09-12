"""Supervisor-owned loopback bridge to an exact isolated E0 candidate, not a general proxy.

No candidate code runs here. The trusted transport selects an immutable container/network
namespace, not a URL returned by the application. Only one seeded form is browser-reachable;
setup, observer, diagnostics and arbitrary upstream paths never cross this boundary.
"""

from __future__ import annotations

import re
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import CodeType, FunctionType
from typing import Any

from accessforge_domain.canonical import digest

from .sandbox import CleanupUnconfirmed, DaemonBinding, SandboxRefused

MAX_BODY = 8192
MAX_RESPONSE = 262144
MAX_HEADERS = 16384
MAX_REQUESTS = 128
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'; "
    "sandbox allow-forms allow-scripts allow-same-origin"
)
PROTOCOL = "owned-reference-loopback-v1"


@dataclass(frozen=True, slots=True)
class CandidateEndpointBinding:
    """Trusted launch facts; never constructed from an application marker or navigator data."""

    task_id: str
    artifact_digest: str
    runtime_policy_digest: str
    candidate_id: str
    driver_id: str
    image_id: str
    daemon: DaemonBinding

    def __post_init__(self) -> None:
        for value in (
            self.artifact_digest,
            self.runtime_policy_digest,
            self.candidate_id,
            self.driver_id,
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise SandboxRefused("gateway binding requires exact immutable identities")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id):
            raise SandboxRefused("gateway binding requires an immutable image")
        if str(uuid.UUID(self.task_id)) != self.task_id:
            raise SandboxRefused("gateway binding requires the trusted task ID")


class _HeaderBudget:
    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.remaining = MAX_HEADERS

    def readline(self, limit: int = -1) -> bytes:
        line: bytes = self.stream.readline(min(limit, self.remaining + 1))
        self.remaining -= len(line)
        if self.remaining < 0:
            raise ValueError("gateway header budget exceeded")
        return line

    def read(self, length: int) -> bytes:
        data: bytes = self.stream.read(length)
        return data

    def close(self) -> None:
        self.stream.close()


class CandidateGateway:
    """One bounded, non-reusable endpoint lifetime owned by a trusted deployment callback.

    The transport must bound Docker I/O and revalidate daemon/process ownership before each
    request. Unconfirmed handler termination fails cleanup. This live route binding is not a
    durable reader/lease attestation or a matched sealed environment manifest.
    """

    def __init__(
        self,
        *,
        binding: CandidateEndpointBinding,
        nonce: str,
        transport: Callable[[str, str, str], dict[str, Any]],
        wall_seconds: int = 30,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
            raise SandboxRefused("gateway requires the exact seeded fixture nonce")
        if not 1 <= wall_seconds <= 60:
            raise SandboxRefused("gateway lifetime must be between 1 and 60 seconds")
        self.binding = binding
        self.path = "/form/" + nonce
        self.transport = transport
        self.wall_seconds = wall_seconds
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._connection: socket.socket | None = None
        self._closed = False
        self._requests = 0
        self._deadline = 0.0
        self._expiry: threading.Timer | None = None

    @property
    def origin(self) -> str:
        if self._server is None or self._closed or time.monotonic() >= self._deadline:
            raise SandboxRefused("candidate endpoint is not live")
        return "http://127.0.0.1:" + str(self._server.server_port)

    @property
    def start_url(self) -> str:
        return self.origin + self.path

    def receipt(self) -> dict[str, Any]:
        # Presence of this receipt is not proof the browser actually navigated here.
        b = self.binding
        identity = {
            "protocol": PROTOCOL,
            "origin": self.origin,
            "path": self.path,
            "taskId": b.task_id,
            "artifactDigest": b.artifact_digest,
            "runtimePolicyDigest": b.runtime_policy_digest,
            "candidateId": b.candidate_id,
            "driverId": b.driver_id,
            "imageId": b.image_id,
            "daemonEndpoint": b.daemon.endpoint,
            "daemonId": b.daemon.daemon_id,
            "contentSecurityPolicy": CSP,
        }
        return {**identity, "bindingDigest": digest(identity)}

    def __enter__(self) -> CandidateGateway:
        if self._server is not None or self._closed:
            raise SandboxRefused("candidate endpoint cannot be reused")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self) -> None:
                super().setup()
                owner._connection = self.connection
                # Absolute deadline includes a peer trickling bytes while keeping each socket
                # read alive. The server is single-threaded: at most one such timer exists.
                self.timer = threading.Timer(2.0, owner._disconnect)
                self.timer.daemon = True
                self.timer.start()
                self.rfile = _HeaderBudget(self.rfile)  # type: ignore[assignment]

            def log_message(self, format: str, *args: Any) -> None:
                pass  # Never log candidate-controlled paths/headers/form contents.

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    self.timer.cancel()
                    self.timer.join()
                    owner._connection = None

            def do_GET(self) -> None:
                self.handle_form()

            def do_POST(self) -> None:
                self.handle_form()

            def handle_form(self) -> None:
                # Header/body parsing has an absolute two-second budget; the trusted backend
                # transport has its independent five-second execution budget after framing.
                try:
                    owner._requests += 1
                    if (
                        owner._closed
                        or time.monotonic() >= owner._deadline
                        or owner._requests > MAX_REQUESTS
                    ):
                        raise SandboxRefused("endpoint execution budget exhausted")
                    host = owner.origin.removeprefix("http://")
                    if (
                        self.headers.get_all("Host") != [host]
                        or self.path != owner.path
                        or self.requestline.split()[1] != owner.path
                    ):
                        raise SandboxRefused("request does not name the sealed endpoint")
                    if self.headers.get("Sec-Fetch-Site") == "cross-site":
                        raise SandboxRefused("cross-site reference navigation refused")
                    if self.headers.get_all("Transfer-Encoding") is not None:
                        raise SandboxRefused("chunked or ambiguous framing refused")
                    sizes = self.headers.get_all("Content-Length")
                    if sizes is None:
                        length = 0
                    elif len(sizes) == 1 and re.fullmatch(r"[0-9]{1,5}", sizes[0]):
                        length = int(sizes[0])
                    else:
                        raise SandboxRefused("ambiguous content length")
                    if length > MAX_BODY or (self.command == "GET" and length):
                        raise SandboxRefused("request body exceeds form budget")
                    if self.command == "POST":
                        if self.headers.get_all("Origin") != [owner.origin]:
                            raise SandboxRefused("form submission requires the sealed origin")
                        if self.headers.get_all("Content-Type") != [
                            "application/x-www-form-urlencoded"
                        ]:
                            raise SandboxRefused("only the owned form encoding is accepted")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise SandboxRefused("truncated form body")
                    body = raw.decode("utf-8", errors="strict")
                    self.timer.cancel()
                    self.timer.join()
                    try:
                        response = owner.transport(self.command, owner.path, body)
                    except Exception:
                        owner._closed = True
                        raise RuntimeError("candidate transport unavailable") from None
                    if owner._closed or time.monotonic() >= owner._deadline:
                        raise SandboxRefused("endpoint expired during candidate response")
                    status = response.get("status")
                    content = response.get("body")
                    if type(status) is not int or status not in (200, 201, 404, 409, 422):
                        raise SandboxRefused("unexpected candidate status; redirects are refused")
                    if not isinstance(content, str):
                        raise SandboxRefused("candidate response is not text")
                    payload = content.encode("utf-8")
                    if len(payload) > MAX_RESPONSE:
                        raise SandboxRefused("candidate response exceeds budget")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Content-Security-Policy", CSP)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                except (SandboxRefused, UnicodeError, ValueError):
                    self.send_error(403, "candidate request refused")
                except Exception:
                    # A failed authority/transport is not automatically retried.
                    owner._closed = True
                    self.send_error(502, "candidate transport unavailable")

        class Server(HTTPServer):
            allow_reuse_address = False

            def handle_error(self, request: Any, client_address: Any) -> None:
                pass  # Malformed/closed sockets never dump private requests into host logs.

        self._server = Server(("127.0.0.1", 0), Handler)
        self._server.timeout = 0.05
        self._deadline = time.monotonic() + self.wall_seconds
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._expiry = threading.Timer(self.wall_seconds, self._expire)
        self._expiry.daemon = True
        try:
            self._thread.start()
            self._expiry.start()
        except Exception:
            self.__exit__()
            raise
        return self

    def _serve(self) -> None:
        assert self._server is not None
        try:
            while not self._closed:
                self._server.handle_request()
        except Exception:
            self._closed = True
        finally:
            self._server.server_close()

    def _expire(self) -> None:
        self._closed = True
        self._disconnect()
        if self._server is not None:
            self._server.server_close()

    def _disconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __exit__(self, *exc: Any) -> None:
        self._closed = True
        if self._expiry is not None:
            self._expiry.cancel()
        self._disconnect()
        if self._server is not None:
            self._server.server_close()
        if self._thread is not None and self._thread.ident is not None:
            # No new thread is needed to stop: resource exhaustion may be the reason for cleanup.
            self._thread.join(6)
            if self._thread.is_alive():
                raise CleanupUnconfirmed("candidate endpoint handler did not stop")
        if self._expiry is not None and self._expiry.ident is not None:
            self._expiry.join(0.2)


def gateway_policy(code_identity: Callable[[CodeType], dict[str, Any]]) -> dict[str, Any]:
    """Bind executable bridge/framing logic and response policy, not a mutable file's bytes."""
    methods: dict[str, Any] = {}
    for cls in (CandidateGateway, CandidateEndpointBinding, _HeaderBudget):
        for name, value in vars(cls).items():
            function = value.fget if isinstance(value, property) else value
            if isinstance(function, FunctionType):
                methods[cls.__name__ + "." + name] = code_identity(function.__code__)
    return {
        "version": PROTOCOL,
        "methods": methods,
        "csp": CSP,
        "body": MAX_BODY,
        "response": MAX_RESPONSE,
        "headers": MAX_HEADERS,
        "requests": MAX_REQUESTS,
    }
