"""Workspace event streams: durable cursors, bounded replay, and honest resets.

The outbox from module 04 already writes an event in the same transaction as the state change it
announces. This module makes those events readable by a client that disconnects and comes back.

Three decisions carry the correctness.

**The cursor is an integer, not a timestamp.** `outbox_message.id` is a BIGSERIAL assigned in commit
order. Two events committed in the same microsecond are indistinguishable by time, so a resume that
skipped everything at-or-before its timestamp cursor would silently drop one — and drop it for the
rest of that connection, with nothing to notice. The module prompt says it directly: timestamps
alone are not replay cursors.

**Falling out of the retention window is a reset, never a partial stream.** A client that resumed
from a truncated window would believe it had seen everything since its cursor. `ReplayGap` says so
and points at the snapshot instead.

**Reconnection never implies completion.** An event stream carries references, not state. A consumer
re-reads authoritative state, and the snapshot is the only thing that answers "what is true now" —
because the alternative is a client that reconnects, sees no further events, and concludes the run
finished successfully.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.timestamps import to_rfc3339_utc

#: How many events one poll returns. Bounded because a client resuming from a very old cursor would
#: otherwise receive an unbounded batch in one response, and the memory cost lands on the server.
MAX_EVENTS_PER_POLL = 200


class EventStreamError(Exception):
    """An event-stream operation was refused."""


class ReplayGap(EventStreamError):
    """The requested cursor is older than what is retained.

    A distinct type because the correct client behaviour is different: not "retry", but "discard
    local state and resynchronise from the snapshot". Reported as an error rather than served as a
    partial stream, because a partial stream is indistinguishable from a complete one to the client
    receiving it.
    """


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One published event, as a client sees it.

    ``reference`` is a reference, never a command and never a copy of state. A consumer re-reads the
    authoritative record; an event carrying the state itself would be state with no revision check,
    delivered at least once, arriving out of order after a reconnect.
    """

    event_id: int
    topic: str
    operation_id: str
    reference: dict[str, Any]
    occurred_at: str

    def as_sse(self) -> str:
        """Server-sent-events framing.

        `id:` is the durable cursor, which is what a browser echoes back in `Last-Event-ID` on
        reconnect without the application doing anything. That is the whole reason this transport
        was chosen over a bare websocket: the resume contract is part of the protocol rather than
        something each client reimplements.
        """
        import json

        payload = json.dumps(
            {
                "eventId": self.event_id,
                "topic": self.topic,
                "operationId": self.operation_id,
                "reference": self.reference,
                "occurredAt": self.occurred_at,
            },
            sort_keys=True,
        )
        return f"id: {self.event_id}\nevent: {self.topic}\ndata: {payload}\n\n"


def retained_from(conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str) -> int:
    row = conn.execute(
        "SELECT retained_from_event_id FROM event_retention WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    return int(row["retained_from_event_id"]) if row else 0


def set_retention_floor(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, floor_event_id: int
) -> None:
    """Declare the oldest event still available for replay.

    Separate from deleting anything. Recording the floor first means a client asking for a purged
    event gets `ReplayGap` rather than an empty page, and the two are opposite messages.
    """
    conn.execute(
        """
        INSERT INTO event_retention (workspace_id, retained_from_event_id, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (workspace_id) DO UPDATE
            SET retained_from_event_id = GREATEST(
                    event_retention.retained_from_event_id, EXCLUDED.retained_from_event_id
                ),
                updated_at = EXCLUDED.updated_at
        """,
        (workspace_id, floor_event_id, to_rfc3339_utc(datetime.now(UTC))),
    )


def read_events(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    after_event_id: int = 0,
    limit: int = MAX_EVENTS_PER_POLL,
) -> list[StreamEvent]:
    """Events after a cursor, in commit order.

    Raises :class:`ReplayGap` when the cursor predates what is retained. Note the boundary: a cursor
    *equal to* the floor is fine — the client has seen that event and wants what came after. Only a
    cursor strictly below it is a gap, and getting that comparison backwards would either reject
    valid resumes or serve truncated ones.
    """
    if limit < 1 or limit > MAX_EVENTS_PER_POLL:
        raise EventStreamError(f"limit must be between 1 and {MAX_EVENTS_PER_POLL}")

    floor = retained_from(conn, workspace_id=workspace_id)
    if after_event_id < floor:
        raise ReplayGap(
            f"events before {floor} are no longer retained and your cursor is {after_event_id}. "
            "This is a reset rather than a short page: serving what remains would leave you "
            "believing you had seen everything since your cursor. Discard local state and "
            "resynchronise from the snapshot."
        )

    rows = conn.execute(
        """
        SELECT id, topic, operation_id, reference, created_at
        FROM outbox_message
        WHERE id > %s AND published_at IS NOT NULL
        ORDER BY id
        LIMIT %s
        """,
        (after_event_id, limit),
    ).fetchall()

    return [
        StreamEvent(
            event_id=int(r["id"]),
            topic=str(r["topic"]),
            operation_id=str(r["operation_id"]),
            reference=dict(r["reference"]),
            occurred_at=str(r["created_at"]),
        )
        for r in rows
    ]


def latest_event_id(conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str) -> int:
    row = conn.execute(
        "SELECT coalesce(max(id), 0) AS hi FROM outbox_message WHERE published_at IS NOT NULL"
    ).fetchone()
    return int(row["hi"]) if row else 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Authoritative current state, plus the cursor it was taken at.

    The cursor is the point. A client applies the snapshot and then resumes from `as_of_event_id`,
    so events already reflected in it are not applied twice and events after it are not missed. A
    snapshot without a cursor is a race with no safe resume.
    """

    as_of_event_id: int
    runs: list[dict[str, Any]]
    taken_at: str


def snapshot(conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str) -> Snapshot:
    """Read current state and the cursor it corresponds to, in one transaction.

    In one transaction on purpose: taken separately, an event committed between the two would be
    either applied twice or missed entirely depending on the order, and which one depends on timing
    nobody controls.
    """
    cursor = latest_event_id(conn, workspace_id=workspace_id)
    runs = [
        {
            "runId": str(r["id"]),
            "status": str(r["status"]),
            "outcome": str(r["outcome"]),
            "revision": int(r["revision"]),
        }
        for r in conn.execute(
            "SELECT id, status, outcome, revision FROM run ORDER BY id LIMIT 500"
        ).fetchall()
    ]
    return Snapshot(as_of_event_id=cursor, runs=runs, taken_at=to_rfc3339_utc(datetime.now(UTC)))


def record_progress(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    consumer_id: str,
    processed_through: int,
) -> None:
    """Advance a consumer's durable position, and never move it backwards.

    `GREATEST` rather than assignment. A consumer that processed events out of order, or a stale
    worker writing after a newer one, would otherwise rewind the position and cause every event in
    between to be delivered again — which is safe for an idempotent consumer and wasteful for
    everyone, and unsafe for one that is only nearly idempotent.
    """
    conn.execute(
        """
        INSERT INTO event_stream_position
            (id, workspace_id, consumer_id, processed_through, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id, consumer_id) DO UPDATE
            SET processed_through = GREATEST(
                    event_stream_position.processed_through, EXCLUDED.processed_through
                ),
                updated_at = EXCLUDED.updated_at
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            consumer_id,
            processed_through,
            to_rfc3339_utc(datetime.now(UTC)),
        ),
    )


def progress_of(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, consumer_id: str
) -> int:
    row = conn.execute(
        "SELECT processed_through FROM event_stream_position "
        "WHERE workspace_id = %s AND consumer_id = %s",
        (workspace_id, consumer_id),
    ).fetchone()
    return int(row["processed_through"]) if row else 0
