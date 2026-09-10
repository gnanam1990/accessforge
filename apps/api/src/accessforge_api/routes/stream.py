"""The live workspace event stream, and the two things it refuses to imply.

Server-sent events over the durable outbox cursor. SSE rather than a websocket for one reason: the
resume contract is part of the protocol. A browser that loses its connection re-sends the last
`id:` it saw in a `Last-Event-ID` header without the application doing anything, so "reconnect
where I left off" is not something each client reimplements slightly differently.

**Reconnecting never implies completion.** An event stream carries references, not state. A client
that reconnected, saw no further events and concluded the run had finished successfully would be
making exactly the inference this product exists to prevent — so the stream says what it is, every
consumer re-reads authoritative state, and `GET /events/snapshot` is the only thing that answers
"what is true now".

**A truncated window is a reset, not a short stream.** If the cursor is older than what retention
holds, the client is told to discard local state and resynchronise from the snapshot, with the
snapshot's cursor named. Serving whatever remains would leave it believing it had seen everything
since its cursor, and a partial stream is indistinguishable from a complete one to the client
receiving it.

Two operational properties that are easy to leave out and expensive to add later:

* **Authority is rechecked on every poll**, not once at connection time. A long-lived stream opened
  by a member who was removed ten minutes ago is a subscription to a workspace they no longer belong
  to, and the connection can outlive the membership by hours.
* **The stream ends on its own.** A bounded lifetime means a client reconnects periodically, which
  is what makes the recheck above a bound on exposure rather than a formality, and it keeps a
  forgotten browser tab from holding a connection open indefinitely.

The producer is an async generator, and that is not stylistic. The first version was a plain
generator, which Starlette iterates in a worker thread — where `time.sleep` cannot be interrupted.
A client that closed its tab left a thread sleeping for the stream's whole remaining lifetime,
holding a database poll cycle it had nobody to send to. A test that read one event and closed the
response hung for five minutes and found it. Every wait here is now `anyio.sleep`, which
cancellation reaches, and the blocking database work runs in a worker thread one poll at a time.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio

# `anyio.to_thread` is a submodule: importing only `anyio` leaves the attribute missing on some
# versions, and the failure is silent here in the worst possible way -- the response has already
# begun with a 200, so the client sees a successful, empty stream and the error goes to a log
# nobody is reading. That is exactly the shape of failure this product exists to argue against, and
# it cost an afternoon to find.
import anyio.to_thread
import psycopg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from accessforge_api.auth import (
    MembershipError,
    SessionError,
    resolve_human_principal,
    resolve_session,
)
from accessforge_api.problems import ProblemCode, ProblemDetail
from accessforge_api.routes._common import authorize, database_url, workspace_scope
from accessforge_domain.authorization import AuthorizationError
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import events, workspace_connection

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["events"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]

#: How long one connection lives. Short enough that a revoked membership stops receiving events
#: promptly even if the per-poll recheck were somehow bypassed, long enough that a client is not
#: reconnecting constantly. SSE clients reconnect on their own, so ending a stream costs nothing.
#:
#: Configurable because a proxy in front of this decides the real ceiling: a load balancer with a
#: 60-second idle timeout will cut a 300-second stream in a way that looks like a server fault. An
#: operator who has to match their infrastructure should not have to fork the code to do it.
MAX_STREAM_SECONDS = float(os.environ.get("ACCESSFORGE_STREAM_MAX_SECONDS", "300"))

#: Time between polls. The outbox is a table, not a notification channel — `LISTEN/NOTIFY` would be
#: lower latency and would also make the stream depend on a session-scoped channel that a connection
#: pool quietly breaks. A second of latency on a progress event is not worth that failure mode.
POLL_SECONDS = float(os.environ.get("ACCESSFORGE_STREAM_POLL_SECONDS", "1.0"))

#: Sent when nothing has happened, so a proxy between here and the client does not decide the
#: connection is dead. A comment line rather than an event: a client must not receive something it
#: could mistake for a state change.
HEARTBEAT = ": heartbeat\n\n"


def _cursor(request: Request, after: int | None) -> int:
    """Where to resume from.

    `Last-Event-ID` wins over the query parameter, because the browser sets it automatically on a
    reconnect and the query parameter is whatever the page happened to remember. A client that
    disagreed with itself should defer to the value the protocol maintained.
    """
    header = request.headers.get("Last-Event-ID")
    if header is not None:
        # `isascii()` as well as `isdigit()`. `str.isdigit()` is true for the whole Unicode digit
        # category -- superscripts, Devanagari, circled numerals -- and `int()` rejects most of
        # them. `Last-Event-ID: ²` passed this guard and raised inside `int()`, turning a
        # well-formed refusal into a 500 with no problem document at all.
        if not (header.isascii() and header.isdigit()):
            raise ProblemDetail(
                ProblemCode.INVALID_INPUT,
                "Last-Event-ID must be an integer event id. The cursor is an outbox row id, not a "
                "timestamp: two events committed in the same microsecond are indistinguishable by "
                "time, and a resume that skipped everything at-or-before a timestamp would "
                "silently drop one.",
            )
        return int(header)
    if after is None:
        return 0
    if after < 0:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, "after must not be negative")
    return after


@router.get("/events/snapshot")
def read_snapshot(
    workspace_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Authoritative current state, and the cursor it was taken at.

    The cursor is the point. A client applies this and then resumes the stream from
    `asOfEventId`, so events already reflected here are not applied twice and events after it are
    not missed. A snapshot without a cursor is a race with no safe resume, and it is the thing a
    `reset` instruction sends a client to.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    # Authorized tenant state, so it must not be written to any cache between here and the reader.
    # Nothing in this application sets a global directive, and a browser or proxy that kept this
    # would serve one workspace's runs to whoever used that machine next.
    response.headers["Cache-Control"] = "no-store"
    taken = events.snapshot(conn, workspace_id=workspace_id)
    return {
        "asOfEventId": taken.as_of_event_id,
        "takenAt": taken.taken_at,
        "runs": taken.runs,
        "resumeFrom": taken.as_of_event_id,
        "meaning": (
            "This is what is true now. Resume the stream from asOfEventId. A run listed here as "
            "still going is still going; nothing in a snapshot or a stream makes a run complete."
        ),
    }


@router.get(
    "/events/stream",
    response_class=StreamingResponse,
    # Declared, because FastAPI infers `application/json` from the return annotation and a consumer
    # generating a client from that contract would build a JSON parser for a stream of SSE frames.
    responses={
        200: {
            "description": (
                "A server-sent event stream. Frames carry `id:` (the durable outbox cursor), "
                "`event:` and a JSON `data:` payload. A `reset` frame means the cursor fell below "
                "the retention floor: discard local state and resynchronise from /events/snapshot."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
def stream_events(
    workspace_id: str, request: Request, after: int | None = None
) -> StreamingResponse:
    """Live events, resumable, with authority rechecked on every poll.

    Deliberately does **not** take the `Conn` dependency every other route uses. FastAPI closes a
    generator dependency after the *response* completes, which for a streaming response means after
    the stream ends — so a subscriber would hold an open transaction for five minutes, showing up in
    `pg_stat_activity` as "idle in transaction" and blocking vacuum on every table it touched. One
    connection is opened here for the authorization check and closed immediately; the stream then
    opens a short-lived one per poll.
    """
    from accessforge_api.auth import SESSION_COOKIE

    url = database_url(request)
    with workspace_connection(url, workspace_id) as opening:
        authorize(opening, request, workspace_id, Permission.EVIDENCE_READ)
    start_cursor = _cursor(request, after)

    session_token = request.cookies.get(SESSION_COOKIE)

    def _poll(cursor: int) -> tuple[str | None, list[events.StreamEvent]]:
        """One database cycle: recheck authority, then read. Runs in a worker thread.

        Returns a terminal frame to emit, or the batch to send. Doing both in one call keeps the
        connection's lifetime to a single poll rather than the stream's, so a subscriber costs a
        pool slot only while it is actually being served.
        """
        with workspace_connection(url, workspace_id) as poll:
            try:
                _assert_still_a_member(poll, session_token, workspace_id)
            except _AccessLost as lost:
                return _frame("access-revoked", {"detail": str(lost)}), []

            try:
                batch = events.read_events(poll, workspace_id=workspace_id, after_event_id=cursor)
            except events.ReplayGap as gap:
                floor = events.retained_from(poll, workspace_id=workspace_id)
                taken = events.snapshot(poll, workspace_id=workspace_id)
                return (
                    _frame(
                        "reset",
                        {
                            "detail": str(gap),
                            "retainedFromEventId": floor,
                            "snapshot": f"/v1/workspaces/{workspace_id}/events/snapshot",
                            "resumeFrom": taken.as_of_event_id,
                        },
                    ),
                    [],
                )
        return None, batch

    async def produce() -> AsyncIterator[str]:
        cursor = start_cursor
        deadline = time.monotonic() + MAX_STREAM_SECONDS

        while time.monotonic() < deadline:
            # A disconnected client is checked before every poll. Without this the generator keeps
            # querying and yielding into a socket nobody is reading, for as long as its lifetime
            # allows, and the cost is paid by the database rather than by the client that left.
            if await request.is_disconnected():
                return

            terminal, batch = await anyio.to_thread.run_sync(_poll, cursor)
            if terminal is not None:
                yield terminal
                return

            if batch:
                for event in batch:
                    yield event.as_sse()
                cursor = batch[-1].event_id
                # Straight back round without sleeping: a full batch usually means more is waiting,
                # and pausing between pages would deliver a burst at one page per second.
                continue

            yield HEARTBEAT
            # anyio.sleep, not time.sleep: this one is cancellable, which is what lets a
            # disconnecting client actually stop the stream instead of merely stopping to read it.
            await anyio.sleep(POLL_SECONDS)

        yield _frame(
            "stream-ended",
            {
                "detail": (
                    "This stream reached its maximum lifetime and closed. Reconnect; your client "
                    "sends Last-Event-ID automatically and will resume from where it stopped. "
                    "This is not a statement that anything finished."
                ),
                "resumeFrom": cursor,
            },
        )

    return StreamingResponse(
        produce(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Proxies that buffer a response defeat the entire point of a stream, and the failure
            # looks like "events arrive in a clump five minutes late" rather than like a bug.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _frame(event: str, payload: dict[str, Any]) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


class _AccessLost(Exception):
    """The subscriber may no longer read this workspace."""


def _assert_still_a_member(
    conn: psycopg.Connection[Any], session_token: str | None, workspace_id: str
) -> None:
    """Recheck authority mid-stream.

    Checked on every poll rather than once at connection time. A stream is the one place in this API
    where a single authorization decision would otherwise cover hours of delivery, and a membership
    revoked at 10:00 must not keep feeding a connection opened at 09:55.

    The message says only that access ended. Which of the three reasons applies — the session
    expired, the session was revoked, the membership was removed — is not something the losing
    party needs, and distinguishing them across a workspace boundary is how a stream becomes an
    oracle for whether a workspace still exists.
    """
    try:
        session = resolve_session(conn, session_token=session_token)
        resolve_human_principal(conn, session=session, workspace_id_from_route=workspace_id)
    except (SessionError, MembershipError, AuthorizationError) as exc:
        raise _AccessLost(
            "your access to this workspace ended while this stream was open, so it has been "
            "closed. Sign in again if you believe this is wrong."
        ) from exc
