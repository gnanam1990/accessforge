"""Real loopback framing/route confinement, with a deliberately simple trusted test transport."""

from __future__ import annotations

import http.client
import socket
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from accessforge_build_worker.candidate_gateway import (
    CSP,
    MAX_HEADERS,
    CandidateEndpointBinding,
    CandidateGateway,
)
from accessforge_build_worker.sandbox import CleanupUnconfirmed, DaemonBinding, SandboxRefused
from accessforge_domain.candidate_endpoint import validate_endpoint_plan
from accessforge_domain.canonical import digest


@pytest.mark.parametrize("occupied", [False, True])
def test_explicit_origin_is_committed_and_never_falls_back_to_another_port(occupied: bool) -> None:
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    origin = f"http://127.0.0.1:{held.getsockname()[1]}"
    events: list[str] = []

    def planned(plan: dict[str, Any]) -> None:
        validate_endpoint_plan(plan)
        assert plan["listenOrigin"] == origin
        events.append("plan")

    def bound(receipt: dict[str, Any]) -> None:
        assert receipt["origin"] == receipt["listenOrigin"] == origin
        assert receipt["bindingDigest"] == digest(
            {k: v for k, v in receipt.items() if k != "bindingDigest"}
        )
        events.append("bound")

    gateway = CandidateGateway(
        binding=binding(),
        nonce="n" * 24,
        transport=lambda *args: {"status": 200, "body": "owned fixture"},
        listen_origin=origin,
        on_planned=planned,
        on_bound=bound,
    )
    try:
        if occupied:
            with pytest.raises(OSError):
                with gateway:
                    pytest.fail("occupied approved port must not bind another port")
            assert events == ["plan"]
        else:
            held.close()
            with gateway:
                assert gateway.origin == origin
                assert request(gateway)[0] == 200
            assert events == ["plan", "bound"]
    finally:
        held.close()


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8081",
        "http://0.0.0.0:8081",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "https://127.0.0.1:8081",
        "http://127.0.0.1:8081/path",
    ],
)
def test_explicit_origin_refuses_nonexact_or_nonlocal_addresses(origin: str) -> None:
    with pytest.raises(SandboxRefused):
        CandidateGateway(
            binding=binding(), nonce="n" * 24, transport=lambda *args: {}, listen_origin=origin
        )


def binding() -> CandidateEndpointBinding:
    return CandidateEndpointBinding(
        task_id=str(uuid.uuid4()),
        artifact_digest="a" * 64,
        runtime_policy_digest="b" * 64,
        candidate_id="c" * 64,
        driver_id="d" * 64,
        image_id="sha256:" + "e" * 64,
        daemon=DaemonBinding("unix:///tmp/test-only.sock", "test"),
    )


def request(
    gateway: CandidateGateway,
    method: str = "GET",
    *,
    path: str | None = None,
    headers: dict[str, str] | None = None,
    body: str = "",
) -> tuple[int, bytes, dict[str, str]]:
    client = http.client.HTTPConnection(gateway.origin.removeprefix("http://"), timeout=8)
    try:
        client.request(method, path or gateway.path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        client.close()


@pytest.mark.parametrize("fault", [None, "artifact", "old", "extra"])
def test_controller_artifact_measurement_requires_fresh_exact_binding(fault: str | None) -> None:
    identity = binding()

    def observe() -> dict[str, Any]:
        result = {
            "taskId": identity.task_id,
            "candidateId": identity.candidate_id,
            "imageId": identity.image_id,
            "daemonId": identity.daemon.daemon_id,
            "artifactDigest": "f" * 64 if fault == "artifact" else identity.artifact_digest,
            "artifactTreeDigest": "e" * 64,
            "observedAt": "2000-01-01T00:00:00Z"
            if fault == "old"
            else datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
        }
        if fault == "extra":
            result["sourceDigest"] = "unmeasured source identity"
        return result

    with CandidateGateway(
        binding=identity,
        nonce="test-fixture-nonce",
        transport=lambda *args: {"status": 200, "body": "ok"},
        observe_artifact=observe,
    ) as gateway:
        if fault is None:
            assert gateway.observe_artifact()["artifactDigest"] == identity.artifact_digest
        else:
            with pytest.raises(SandboxRefused, match="artifact measurement unavailable"):
                gateway.observe_artifact()
            with pytest.raises(SandboxRefused):
                _ = gateway.origin


@pytest.mark.parametrize("fail_at", [None, "plan", "bound", "admit", "cleanup"])
def test_committed_lifecycle_precedes_admission_and_confirms_actual_close(
    fail_at: str | None,
) -> None:
    events: list[str] = []

    def planned(identity: dict[str, Any]) -> None:
        assert gateway._server is None
        assert identity == gateway.plan()
        events.append("plan")
        if fail_at == "plan":
            raise RuntimeError("plan failure")

    def bound(receipt: dict[str, Any]) -> None:
        assert gateway._server is not None and gateway._thread is None
        assert receipt == gateway.receipt()
        events.append("bound")
        if fail_at == "bound":
            raise RuntimeError("bound failure")

    def admit() -> None:
        assert events == ["plan", "bound"] and gateway._thread is None
        events.append("admit")
        if fail_at == "admit":
            raise RuntimeError("admit failure")

    def closed(clean: bool) -> None:
        assert clean and gateway._closed
        assert gateway._server is not None and gateway._server.socket.fileno() == -1
        assert gateway._thread is None or not gateway._thread.is_alive()
        events.append("cleanup")
        if fail_at == "cleanup":
            raise RuntimeError("cleanup failure")

    gateway = CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: {"status": 200, "body": "ok"},
        on_planned=planned,
        on_bound=bound,
        on_admit=admit,
        on_closed=closed,
    )
    if fail_at:
        expected = CleanupUnconfirmed if fail_at == "cleanup" else RuntimeError
        with pytest.raises(expected):
            with gateway:
                assert fail_at == "cleanup"
    else:
        with gateway:
            assert request(gateway)[0] == 200
        gateway.__exit__()
    assert (
        events
        == {
            None: ["plan", "bound", "admit", "cleanup"],
            "plan": ["plan"],
            "bound": ["plan", "bound", "cleanup"],
            "admit": ["plan", "bound", "admit", "cleanup"],
            "cleanup": ["plan", "bound", "admit", "cleanup"],
        }[fail_at]
    )


def test_binding_persistence_cannot_delay_listener_expiry() -> None:
    def stalled(receipt: dict[str, Any]) -> None:
        port = int(receipt["origin"].rsplit(":", 1)[1])
        assert gateway._expiry is not None
        gateway._expiry.join(2)
        assert not gateway._expiry.is_alive()
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass

    gateway = CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        wall_seconds=1,
        transport=lambda *args: {},
        on_bound=stalled,
    )
    with pytest.raises(SandboxRefused, match="expired"):
        with gateway:
            pytest.fail("expired binding was admitted")


def test_exact_route_has_trusted_binding_and_no_candidate_response_headers() -> None:
    calls: list[tuple[str, str, str]] = []

    def transport(method: str, path: str, body: str) -> dict[str, Any]:
        calls.append((method, path, body))
        return {
            "status": 422,
            "body": "<h1>Validation failed</h1>",
            "headers": {"Set-Cookie": "private=bad", "Location": "https://example.test"},
        }

    with CandidateGateway(binding=binding(), nonce="test-fixture-nonce", transport=transport) as g:
        status, body, headers = request(g)
        assert status == 422 and body == b"<h1>Validation failed</h1>"
        assert headers["Content-Security-Policy"] == CSP
        assert headers["Cache-Control"] == "no-store"
        assert "Set-Cookie" not in headers and "Location" not in headers
        result = g.receipt()
        identity = dict(result)
        assert identity.pop("bindingDigest") == digest(identity)
        assert result["candidateId"] == g.binding.candidate_id
        assert result["artifactDigest"] == g.binding.artifact_digest
        assert calls == [("GET", g.path, "")]
        origin = g.origin
    with pytest.raises(SandboxRefused, match="not live"):
        g.receipt()
    with pytest.raises(SandboxRefused, match="reused"):
        g.__enter__()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", int(origin.rsplit(":", 1)[1])), timeout=1)


@pytest.mark.parametrize(
    "path",
    [
        "/api/_test/reset",
        "/api/_test/fixtures",
        "/api/_test/receipt/test-fixture-nonce",
        "/diagnostics",
        "/form/another-fixture",
        "/form/test-fixture-nonce?x=1",
        "http://example.test/form/test-fixture-nonce",
        "/form/%74est-fixture-nonce",
        "//form/test-fixture-nonce",
    ],
)
def test_no_alternate_route_reaches_candidate(path: str) -> None:
    def transport(*args: Any) -> dict[str, Any]:
        pytest.fail("forbidden request reached candidate")

    with CandidateGateway(binding=binding(), nonce="test-fixture-nonce", transport=transport) as g:
        assert request(g, path=path)[0] == 403


@pytest.mark.parametrize(
    "method,headers,body",
    [
        ("GET", {"Host": "evil.test"}, ""),
        ("GET", {"Sec-Fetch-Site": "cross-site"}, ""),
        ("GET", {}, "unexpected body"),
        ("POST", {}, "x=1"),
        (
            "POST",
            {"Origin": "http://evil.test", "Content-Type": "application/x-www-form-urlencoded"},
            "x=1",
        ),
        ("POST", {"Transfer-Encoding": "chunked"}, "x=1"),
        ("POST", {"Content-Length": "99999"}, ""),
    ],
)
def test_unsealed_or_ambiguous_request_never_reaches_transport(
    method: str,
    headers: dict[str, str],
    body: str,
) -> None:
    def transport(*args: Any) -> dict[str, Any]:
        pytest.fail("unsealed request reached candidate")

    with CandidateGateway(binding=binding(), nonce="test-fixture-nonce", transport=transport) as g:
        assert request(g, method, headers=headers, body=body)[0] == 403


def test_same_origin_form_is_forwarded_without_credentials_or_browser_headers() -> None:
    calls: list[tuple[str, str, str]] = []

    def transport(*args: str) -> dict[str, Any]:
        calls.append(args)  # type: ignore[arg-type]
        return {"status": 201, "body": "created"}

    with CandidateGateway(binding=binding(), nonce="test-fixture-nonce", transport=transport) as g:
        result = request(
            g,
            "POST",
            body="full_name=Synthetic",
            headers={
                "Origin": g.origin,
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "unrelated=must-not-forward",
                "Authorization": "test-only",
            },
        )
        assert result[0] == 201
        assert calls == [("POST", g.path, "full_name=Synthetic")]


@pytest.mark.parametrize(
    "response",
    [
        {"status": 302, "body": "redirect"},
        {"status": True, "body": "bad status"},
        {"status": 200, "body": "x" * 262145},
        {"status": 200, "body": ["not text"]},
    ],
)
def test_unsafe_candidate_response_is_refused(response: dict[str, Any]) -> None:
    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: response,
    ) as g:
        assert request(g)[0] == 403


def test_header_budget_and_absolute_slow_peer_deadline() -> None:
    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: {"status": 200, "body": "unused"},
    ) as g:
        address = ("127.0.0.1", int(g.origin.rsplit(":", 1)[1]))
        with socket.create_connection(address, timeout=4) as client:
            client.sendall(
                b"GET /form/test-fixture-nonce HTTP/1.1\r\nX: " + b"x" * MAX_HEADERS + b"\r\n\r\n"
            )
            assert client.recv(1024) == b""
        with socket.create_connection(address, timeout=4) as client:
            client.sendall(b"GET /form/test-fixture-nonce HTTP/1.1\r\n")
            start = time.monotonic()
            assert client.recv(1024) == b""
            assert time.monotonic() - start < 3.5


def test_expired_route_refuses_new_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: {"status": 200, "body": "unused"},
    ) as g:
        monkeypatch.setattr(g, "_deadline", time.monotonic() - 1)
        with pytest.raises(SandboxRefused, match="not live"):
            g.receipt()


def test_real_expiry_closes_listener_without_waiting_for_callback() -> None:
    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        wall_seconds=1,
        transport=lambda *args: {"status": 200, "body": "unused"},
    ) as g:
        port = int(g.origin.rsplit(":", 1)[1])
        assert g._server is not None
        g._server.timeout = 5  # Linux select can retain a closed descriptor until it wakes.
        assert g._expiry is not None
        g._expiry.join(2)
        assert not g._expiry.is_alive()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)
        with pytest.raises(SandboxRefused, match="not live"):
            g.receipt()


def test_transport_refusal_fences_endpoint_against_retry() -> None:
    calls = 0

    def transport(*args: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise SandboxRefused("the original daemon changed")

    with CandidateGateway(binding=binding(), nonce="test-fixture-nonce", transport=transport) as g:
        origin = g.origin
        assert request(g)[0] == 502
        client = http.client.HTTPConnection(origin.removeprefix("http://"), timeout=2)
        try:
            with pytest.raises((ConnectionRefusedError, ConnectionResetError)):
                client.request("GET", g.path)
                client.getresponse()
        finally:
            client.close()
        assert calls == 1


@pytest.mark.parametrize("stage", [1, 2])
def test_failed_server_or_timer_start_closes_listener(
    monkeypatch: pytest.MonkeyPatch,
    stage: int,
) -> None:
    gateway = CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: {},
    )
    start = threading.Thread.start
    attempts = 0

    def fail_once(thread: threading.Thread) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == stage:
            raise RuntimeError("synthetic thread exhaustion")
        start(thread)

    with monkeypatch.context() as context:
        context.setattr(threading.Thread, "start", fail_once)
        with pytest.raises(RuntimeError, match="synthetic"):
            gateway.__enter__()
    assert gateway._server is not None
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", gateway._server.server_port), timeout=1)
    if stage == 1:  # Expiry timer failure prevents even creating the serving thread.
        assert gateway._thread is None
    else:
        assert gateway._thread is not None and not gateway._thread.is_alive()


def test_duplicate_lengths_are_rejected_even_when_equal() -> None:
    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        transport=lambda *args: pytest.fail("ambiguous framing reached candidate"),
    ) as g:
        client = http.client.HTTPConnection(g.origin.removeprefix("http://"), timeout=2)
        try:
            client.putrequest("GET", g.path)
            client.putheader("Content-Length", "0")
            client.putheader("Content-Length", "0")
            client.endheaders()
            response = client.getresponse()
            assert response.status == 403
            response.read()
        finally:
            client.close()


def test_expiry_disconnects_a_transport_still_in_flight() -> None:
    def late(*args: Any) -> dict[str, Any]:
        threading.Event().wait(1.2)
        return {"status": 200, "body": "must not be delivered after expiry"}

    with CandidateGateway(
        binding=binding(),
        nonce="test-fixture-nonce",
        wall_seconds=1,
        transport=late,
    ) as g:
        with pytest.raises(ConnectionResetError):
            request(g)
