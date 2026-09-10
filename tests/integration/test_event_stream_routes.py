"""The live event stream, tested against a real server because a test client cannot see one.

Not "does it deliver an event" — that is the easy half. These are the four properties that make a
stream safe to build a UI on, each of which fails silently when it is missing:

* a truncated retention window produces a **reset**, not a short stream;
* a resume cursor is an **outbox id**, never a timestamp;
* authority is rechecked **while the stream is open**, not once when it opened;
* nothing the stream says can be read as "the run finished".

**These run against a real uvicorn process.** Starlette's `TestClient` buffers a streaming response
in full before returning any of it — measured, not assumed: a five-event generator sleeping two
seconds between events yielded nothing for ten seconds and then everything at once. Every assertion
below is about what arrives *while the stream is open*, so a client that can only see the end of it
proves none of them. The subprocess costs a few seconds per module and is the difference between
testing this and appearing to.

Requirements: FR-015, FR-017. Invariants: INV-06, INV-12.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from accessforge_api.auth import SESSION_COOKIE
from accessforge_persistence import (
    assert_row_level_security_enforced,
    events,
    migrate,
    outbox,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
WS = str(uuid.UUID(int=0x330))
OWNER = str(uuid.UUID(int=0x331))

#: The server under test runs with a short stream lifetime and a fast poll, through the same
#: environment variables an operator would use behind a proxy with a short idle timeout. Overriding
#: module constants would not reach a subprocess, and reaching into the module under test to make it
#: testable usually means testing something the deployment never runs.
STREAM_MAX_SECONDS = "4"
STREAM_POLL_SECONDS = "0.05"


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Stream')", (WS,))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, 'o@example.test')", (OWNER,))
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
    yield test_database_url


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def server(request: pytest.FixtureRequest) -> Iterator[str]:
    """A real uvicorn process, so a streaming response actually streams."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not configured; this suite cannot run against nothing")

    port = _free_port()
    environment = {
        **os.environ,
        "ACCESSFORGE_DATABASE_URL": url,
        "ACCESSFORGE_EVIDENCE_ENDPOINT_URL": os.environ.get(
            "OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "ACCESSFORGE_EVIDENCE_BUCKET": os.environ.get(
            "OBJECT_STORE_BUCKET", "accessforge-evidence"
        ),
        "ACCESSFORGE_EVIDENCE_ACCESS_KEY": os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        "ACCESSFORGE_EVIDENCE_SECRET_KEY": os.environ.get(
            "OBJECT_STORE_SECRET_KEY", "unset-for-this-test"
        ),
        "ACCESSFORGE_ENVIRONMENT": "test",
        "ACCESSFORGE_PORT": str(port),
        "ACCESSFORGE_STREAM_MAX_SECONDS": STREAM_MAX_SECONDS,
        "ACCESSFORGE_STREAM_POLL_SECONDS": STREAM_POLL_SECONDS,
    }
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "accessforge_api"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError(
                    f"the server exited: {(process.stdout or None) and process.stdout.read()}"
                )
            try:
                if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:  # pragma: no cover - only when the server never starts
            raise AssertionError("the server never became live")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged server
            process.kill()


@pytest.fixture()
def client(db: str, server: str) -> Iterator[httpx.Client]:
    with workspace_connection(db, WS) as conn:
        from accessforge_api.auth import issue_session

        issued = issue_session(conn, user_id=OWNER)
    with httpx.Client(
        base_url=server, timeout=20, cookies={SESSION_COOKIE: issued.session_token}
    ) as c:
        yield c


@pytest.fixture()
def anonymous(server: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server, timeout=20) as c:
        yield c


def _publish(db: str, topic: str, reference: dict[str, str]) -> int:
    """Enqueue an outbox message and mark it published, as the publisher worker would."""
    with workspace_connection(db, WS) as conn:
        message_id = outbox.enqueue_message(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            topic=topic,
            reference=reference,
        )
        outbox.mark_published(conn, message_id=message_id)
        return message_id


def _frames(body: str) -> list[dict[str, str]]:
    """Parse an SSE body into frames, keeping comments out of the result.

    A heartbeat is a comment line precisely so that no client can mistake it for a state change, and
    a parser that turned one into an event would defeat that.
    """
    frames = []
    for block in body.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        frame: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line and not line.startswith(":"):
                key, _, value = line.partition(":")
                frame[key.strip()] = value.strip()
        if frame:
            frames.append(frame)
    return frames


# --- the snapshot --------------------------------------------------------------------------------


def test_the_snapshot_carries_the_cursor_it_was_taken_at(db: str, client: httpx.Client) -> None:
    """A snapshot without a cursor is a race with no safe resume.

    Apply the snapshot, resume from `asOfEventId`: events already reflected in it are not applied
    twice and events after it are not missed. Taken in one transaction, so an event committed
    between the read and the cursor cannot fall into the gap.
    """
    _publish(db, "run.requested", {"runId": "r1"})
    body = client.get(f"/v1/workspaces/{WS}/events/snapshot").json()
    assert body["asOfEventId"] >= 1
    assert body["resumeFrom"] == body["asOfEventId"]
    assert "nothing in a snapshot or a stream makes a run complete" in body["meaning"]


# --- resuming ------------------------------------------------------------------------------------


def test_events_after_the_cursor_are_delivered_with_their_ids(
    db: str, client: httpx.Client
) -> None:
    first = _publish(db, "run.requested", {"runId": "r1"})
    second = _publish(db, "run.started", {"runId": "r1"})

    with client.stream(
        "GET", f"/v1/workspaces/{WS}/events/stream", params={"after": first}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = _read_until(response, "run.started")

    frames = _frames(body)
    assert [f["event"] for f in frames] == ["run.started"]
    # `id:` is the durable cursor a browser echoes back on reconnect, so it must be the outbox row
    # id rather than anything derived from it.
    assert frames[0]["id"] == str(second)


def test_last_event_id_takes_precedence_over_the_query_parameter(
    db: str, client: httpx.Client
) -> None:
    """The browser maintains `Last-Event-ID`; the query parameter is whatever the page remembered.

    A client that disagrees with itself should defer to the value the protocol kept.
    """
    first = _publish(db, "run.requested", {"runId": "r1"})
    _publish(db, "run.started", {"runId": "r1"})

    with client.stream(
        "GET",
        f"/v1/workspaces/{WS}/events/stream",
        params={"after": 0},
        headers={"Last-Event-ID": str(first)},
    ) as response:
        body = _read_until(response, "run.started")

    assert [f["event"] for f in _frames(body)] == ["run.started"]


@pytest.mark.parametrize("cursor", ["2026-09-10T12:00:00Z", "12.5", "-1", "1e3", ""])
def test_a_cursor_that_is_not_a_plain_integer_is_refused(
    db: str, client: httpx.Client, cursor: str
) -> None:
    response = client.get(f"/v1/workspaces/{WS}/events/stream", headers={"Last-Event-ID": cursor})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_INPUT"


def test_a_unicode_digit_cursor_is_refused_rather_than_crashing(
    db: str, client: httpx.Client, server: str
) -> None:
    """`str.isdigit()` is true for the whole Unicode digit category; `int()` rejects most of it.

    `Last-Event-ID: \u00b2` passed an `isdigit()` guard and raised inside `int()`, so a caller got a
    500 with no problem document rather than the 400 the route intends.

    Sent over a raw socket, because httpx refuses to put a non-ASCII value in a header -- which is
    exactly why a test through the client library reports this unreachable. HTTP header values are
    latin-1 on the wire, Starlette decodes them as latin-1, and byte 0xB2 arrives in the handler as
    a one-character string that `isdigit()` calls a digit. Reachable, and only visible to a test
    that speaks the protocol rather than using a library that protects it from itself.
    """
    host, port = server.removeprefix("http://").split(":")
    token = client.cookies.get(SESSION_COOKIE)
    request = (
        f"GET /v1/workspaces/{WS}/events/stream HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Cookie: {SESSION_COOKIE}={token}\r\n"
        "Last-Event-ID: \u00b2\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")

    with socket.create_connection((host, int(port)), timeout=15) as raw:
        raw.sendall(request)
        received = b""
        while chunk := raw.recv(4096):
            received += chunk

    head, _, body = received.partition(b"\r\n\r\n")
    assert b"400 Bad Request" in head, head[:200]
    assert b"INVALID_INPUT" in body, body[:400]


def test_the_snapshot_is_never_cached(db: str, client: httpx.Client) -> None:
    """Authorized tenant state must not sit in a browser or proxy cache.

    Nothing in this application sets a global directive, so a response without one is one a shared
    machine can serve to whoever uses it next.
    """
    response = client.get(f"/v1/workspaces/{WS}/events/snapshot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_the_contract_declares_the_stream_as_an_event_stream(server: str) -> None:
    """FastAPI infers `application/json` from the return annotation.

    A consumer generating a client from that contract would build a JSON parser for a stream of SSE
    frames — and find out at runtime, against a live subscription.
    """
    published = httpx.get(f"{server}/openapi.json", timeout=10).json()
    operation = published["paths"]["/v1/workspaces/{workspace_id}/events/stream"]["get"]
    assert list(operation["responses"]["200"]["content"]) == ["text/event-stream"]


def test_a_timestamp_shaped_cursor_is_refused(db: str, client: httpx.Client) -> None:
    """The cursor is an outbox row id, not a time.

    Two events committed in the same microsecond are indistinguishable by timestamp, so a resume
    that skipped everything at-or-before a time cursor would silently drop one — and drop it for the
    rest of that connection, with nothing to notice.
    """
    response = client.get(
        f"/v1/workspaces/{WS}/events/stream",
        headers={"Last-Event-ID": "2026-09-10T12:00:00Z"},
    )
    assert response.status_code == 400
    assert "not a timestamp" in response.json()["detail"]


# --- the reset -----------------------------------------------------------------------------------


def test_a_cursor_below_the_retention_floor_produces_a_reset_not_a_short_stream(
    db: str, client: httpx.Client
) -> None:
    """The failure this whole design exists to prevent.

    Serving what remains would leave the client believing it had seen everything since its cursor,
    and a partial stream is indistinguishable from a complete one to the client receiving it. The
    reset names the snapshot and the cursor to resume from, so the client has somewhere to go.
    """
    _publish(db, "run.requested", {"runId": "r1"})
    latest = _publish(db, "run.started", {"runId": "r1"})
    with workspace_connection(db, WS) as conn:
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=latest)

    with client.stream(
        "GET", f"/v1/workspaces/{WS}/events/stream", params={"after": 0}
    ) as response:
        body = _read_until(response, "reset")

    frame = _frames(body)[0]
    assert frame["event"] == "reset"
    payload = json.loads(frame["data"])
    assert payload["retainedFromEventId"] == latest
    assert payload["snapshot"].endswith("/events/snapshot")
    assert payload["resumeFrom"] >= latest
    assert "Discard local state" in payload["detail"]


# --- authority -----------------------------------------------------------------------------------


def test_an_unauthenticated_subscriber_is_refused_before_the_stream_opens(
    db: str, anonymous: httpx.Client
) -> None:
    response = anonymous.get(f"/v1/workspaces/{WS}/events/stream")
    assert response.status_code == 401


def test_a_membership_revoked_mid_stream_closes_it(db: str, client: httpx.Client) -> None:
    """Authority is rechecked on every poll, not once at connection time.

    A stream is the one place in this API where a single authorization decision would otherwise
    cover hours of delivery. A membership revoked at 10:00 must not keep feeding a connection opened
    at 09:55.
    """
    with client.stream(
        "GET", f"/v1/workspaces/{WS}/events/stream", params={"after": 0}
    ) as response:
        assert response.status_code == 200
        # A *scoped* connection. An unscoped one sees no rows of a tenant table -- the isolation
        # policy compares against `current_workspace_id()`, which is NULL there -- so the DELETE
        # would match nothing, report success, and this test would quietly assert on a membership
        # that had never been removed. The same silent no-op that `restore.assert_can_reconcile`
        # exists to prevent, reproduced here by writing the test the obvious way.
        with workspace_connection(db, WS) as conn:
            removed = conn.execute(
                "DELETE FROM workspace_membership WHERE workspace_id = %s AND user_id = %s",
                (WS, OWNER),
            ).rowcount
        assert removed == 1
        body = _read_until(response, "access-revoked")

    frame = _frames(body)[-1]
    assert frame["event"] == "access-revoked"
    payload = json.loads(frame["data"])
    # It says access ended, and not which of the three reasons applies. Distinguishing an expired
    # session from a removed membership across a workspace boundary is how a stream becomes an
    # oracle for whether that workspace exists.
    assert "your access to this workspace ended" in payload["detail"]
    assert "membership" not in payload["detail"]


# --- what the stream never claims ----------------------------------------------------------------


def test_the_stream_ending_is_not_a_statement_that_anything_finished(
    db: str, client: httpx.Client
) -> None:
    """A client that reconnects, sees nothing, and concludes the run passed is the whole problem.

    The server under test runs with a four-second lifetime, so this waits for a real stream to reach
    its real end rather than asserting on a constant.
    """
    last = _publish(db, "run.started", {"runId": "r1"})

    with client.stream(
        "GET", f"/v1/workspaces/{WS}/events/stream", params={"after": last}
    ) as response:
        body = _read_until(response, "stream-ended")
    frame = _frames(body)[-1]
    assert frame["event"] == "stream-ended"
    payload = json.loads(frame["data"])
    assert payload["resumeFrom"] == last
    assert "not a statement that anything finished" in payload["detail"]


def test_a_heartbeat_is_a_comment_and_never_an_event(db: str, client: httpx.Client) -> None:
    """A client must not receive something it could mistake for a state change."""
    with client.stream("GET", f"/v1/workspaces/{WS}/events/stream") as response:
        body = _read_until(response, "stream-ended")
    assert ": heartbeat" in body
    assert [f["event"] for f in _frames(body)] == ["stream-ended"]


def test_a_client_that_disconnects_stops_the_stream_rather_than_leaving_it_running(
    db: str, client: httpx.Client
) -> None:
    """The defect that made the first version of this suite hang.

    The producer was a plain generator, which Starlette iterates in a worker thread where
    `time.sleep` cannot be interrupted — so closing the response left the stream polling the
    database on behalf of a client that had gone, for its whole remaining lifetime. Reading one
    event and closing must return promptly, and "promptly" here means well inside the server's
    four-second stream lifetime.
    """
    _publish(db, "run.started", {"runId": "r1"})
    started = time.monotonic()
    with client.stream("GET", f"/v1/workspaces/{WS}/events/stream", params={"after": 0}) as r:
        _read_until(r, "run.started")
    elapsed = time.monotonic() - started
    assert elapsed < float(STREAM_MAX_SECONDS), (
        f"closing the response took {elapsed:.1f}s, which is the stream's whole lifetime: the "
        "generator ignored the disconnect and ran to completion"
    )


def _read_until(response: httpx.Response, event: str, limit: int = 4000) -> str:
    """Accumulate the stream until the named event's frame is **complete**, then stop.

    Complete, not merely started. An SSE frame is terminated by a blank line, and an earlier version
    returned as soon as it saw the `event:` line -- cutting the `data:` line off and producing a
    `KeyError: 'data'` that looked like the server had sent a frame without a payload. A test that
    misreads the protocol reports a bug in the thing it is testing.

    Bounded, so a test that would otherwise wait for the stream's whole lifetime fails with a
    readable message instead of a timeout.
    """
    collected = ""
    seen = False
    for line in response.iter_lines():
        collected += line + "\n"
        if f"event: {event}" in collected:
            seen = True
        # A blank line ends the frame. `iter_lines` strips terminators, so it arrives as "".
        if seen and line == "":
            return collected
        limit -= 1
        if limit <= 0:  # pragma: no cover - only on a genuine failure
            raise AssertionError(f"never saw a complete {event!r} frame; got:\n{collected[:2000]}")
    return collected
