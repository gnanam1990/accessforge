"""The single trusted evidence sequencer.

CONTRACTS section 7 requires **one trusted ingestion sequencer per attempt**, serialized by a
database lock. Producers submit authenticated records; they do not choose their canonical position.
That distinction is what the canonical hash chain actually attests: ingestion provenance, not
physical truth.

Four separate identities are at work here, and conflating any two of them breaks something:

* **canonical sequence** — the sequencer's ordering of admitted records, starting at 1.
* **producer sequence** — each producer's own ordering, used to detect its gaps.
* **source record id** — the producer's identity for a record, used for replay detection.
* **source record digest** — the content, used to tell a replay from a conflict.

Replay is keyed on producer plus source record id. Same id with the same digest is a replay and is
idempotent; same id with a different digest is a conflict and is rejected, because one of the two
submissions is wrong and guessing which would corrupt the chain.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest

# A fixed, documented genesis hash. Sequence 1 chains from this, so a chain cannot be silently
# re-rooted by inventing a different first previousEventHash.
GENESIS_HASH = hashlib.sha256(b"accessforge:evidence:genesis:v1").hexdigest()


class SequencerError(Exception):
    """A record could not be admitted to the canonical chain."""


class SourceRecordConflict(SequencerError):
    """The same source record id arrived with different content."""


@dataclass(frozen=True, slots=True)
class AdmittedEvent:
    event_id: str
    sequence: int
    previous_event_hash: str
    is_replay: bool


def _lock_attempt(conn: psycopg.Connection[dict[str, Any]], attempt_id: str) -> None:
    """Serialize all sequencing for one attempt.

    A transaction-scoped advisory lock keyed on the attempt. Two sequencer instances handling the
    same attempt would otherwise both read the same tail and assign the same next sequence; the
    unique constraint would catch it, but as a crash rather than as serialization.

    The lock is taken on a hash of the attempt id, so distinct attempts never contend.
    """
    key = int.from_bytes(hashlib.sha256(attempt_id.encode()).digest()[:8], "big", signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))


def _chain_tail(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, attempt_id: str
) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT sequence, payload_digest, previous_event_hash, event_type, source_time
        FROM canonical_event
        WHERE run_id = %s AND attempt_id = %s
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (run_id, attempt_id),
    ).fetchone()

    if row is None:
        return 0, GENESIS_HASH

    # The chain link hashes the admitted content, excluding receivedTime (assigned by ingestion and
    # therefore not something a producer can be held to) and excluding the hash itself.
    link = digest(
        {
            "sequence": int(row["sequence"]),
            "eventType": str(row["event_type"]),
            "sourceTime": row["source_time"].isoformat().replace("+00:00", "Z"),
            "payloadDigest": str(row["payload_digest"]),
            "previousEventHash": str(row["previous_event_hash"]),
        }
    )
    return int(row["sequence"]), link


def admit_record(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    lease_epoch: int,
    producer_id: str,
    source_record_id: str,
    producer_sequence: int,
    event_type: str,
    manifest_digest: str,
    payload: dict[str, Any],
    source_time: datetime,
    now: datetime | None = None,
) -> AdmittedEvent:
    """Admit one producer record to the canonical chain.

    The caller is responsible for having verified that this producer's service identity may submit
    this event type — that ACL is module 03's, and duplicating it here would create a second
    definition of it.
    """
    moment = now or datetime.now(UTC)
    payload_digest = digest(payload)

    _lock_attempt(conn, attempt_id)

    existing = conn.execute(
        """
        SELECT source_record_digest, producer_sequence
        FROM producer_source_record
        WHERE run_id = %s AND attempt_id = %s AND producer_id = %s AND source_record_id = %s
        """,
        (run_id, attempt_id, producer_id, source_record_id),
    ).fetchone()

    if existing is not None:
        if str(existing["source_record_digest"]) != payload_digest:
            raise SourceRecordConflict(
                f"producer {producer_id!r} resubmitted source record {source_record_id!r} with "
                "different content; one of the two submissions is wrong and the chain will not "
                "guess which"
            )
        # A genuine replay: return the position already assigned rather than appending a duplicate.
        row = conn.execute(
            """
            SELECT e.event_id, e.sequence, e.previous_event_hash
            FROM canonical_event e
            WHERE e.run_id = %s AND e.attempt_id = %s AND e.payload_digest = %s
            ORDER BY e.sequence
            LIMIT 1
            """,
            (run_id, attempt_id, payload_digest),
        ).fetchone()
        if row is None:  # pragma: no cover - source record without its event would be a bug
            raise SequencerError(
                f"source record {source_record_id!r} is recorded but has no canonical event"
            )
        return AdmittedEvent(
            event_id=str(row["event_id"]),
            sequence=int(row["sequence"]),
            previous_event_hash=str(row["previous_event_hash"]),
            is_replay=True,
        )

    stream = conn.execute(
        """
        SELECT admitted_through, closed_at_sequence
        FROM producer_stream
        WHERE run_id = %s AND attempt_id = %s AND producer_id = %s
        FOR UPDATE
        """,
        (run_id, attempt_id, producer_id),
    ).fetchone()

    if stream is None:
        conn.execute(
            """
            INSERT INTO producer_stream (workspace_id, run_id, attempt_id, producer_id)
            VALUES (%s, %s, %s, %s)
            """,
            (workspace_id, run_id, attempt_id, producer_id),
        )
        admitted_through = 0
    else:
        if stream["closed_at_sequence"] is not None:
            raise SequencerError(
                f"producer {producer_id!r} has already submitted its closing watermark; a record "
                "after the tail would mean the stream was not finished when it said it was"
            )
        admitted_through = int(stream["admitted_through"])

    # Per-producer contiguity. A gap means earlier records are still in flight, and admitting out of
    # order would let the chain look complete while a producer's middle is missing.
    if producer_sequence != admitted_through + 1:
        raise SequencerError(
            f"producer {producer_id!r} submitted sequence {producer_sequence} but is admitted "
            f"through {admitted_through}; records are staged until contiguous"
        )

    last_sequence, previous_hash = _chain_tail(conn, run_id=run_id, attempt_id=attempt_id)
    sequence = last_sequence + 1
    event_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO canonical_event
            (workspace_id, run_id, attempt_id, sequence, event_id, event_type, lease_epoch,
             source_time, received_time, manifest_digest, previous_event_hash, payload_digest,
             payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            workspace_id,
            run_id,
            attempt_id,
            sequence,
            event_id,
            event_type,
            lease_epoch,
            source_time,
            moment,
            manifest_digest,
            previous_hash,
            payload_digest,
            Jsonb(payload),
        ),
    )
    conn.execute(
        """
        INSERT INTO producer_source_record
            (workspace_id, run_id, attempt_id, producer_id, source_record_id,
             source_record_digest, producer_sequence, received_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            workspace_id,
            run_id,
            attempt_id,
            producer_id,
            source_record_id,
            payload_digest,
            producer_sequence,
            moment,
        ),
    )
    conn.execute(
        """
        UPDATE producer_stream SET admitted_through = %s
        WHERE run_id = %s AND attempt_id = %s AND producer_id = %s
        """,
        (producer_sequence, run_id, attempt_id, producer_id),
    )

    return AdmittedEvent(
        event_id=event_id,
        sequence=sequence,
        previous_event_hash=previous_hash,
        is_replay=False,
    )


def close_producer_stream(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    attempt_id: str,
    producer_id: str,
    final_producer_sequence: int,
    now: datetime | None = None,
) -> None:
    """Record an authenticated closing watermark.

    The watermark must match what was actually admitted. A producer claiming to have finished at a
    sequence beyond its admitted tail is claiming records the sequencer never saw, which is exactly
    the missing-tail case that must prevent verified completion (INV-06).
    """
    _lock_attempt(conn, attempt_id)
    row = conn.execute(
        """
        SELECT admitted_through, closed_at_sequence
        FROM producer_stream
        WHERE run_id = %s AND attempt_id = %s AND producer_id = %s
        FOR UPDATE
        """,
        (run_id, attempt_id, producer_id),
    ).fetchone()

    if row is None:
        raise SequencerError(f"producer {producer_id!r} has no stream for this attempt")
    if row["closed_at_sequence"] is not None:
        if int(row["closed_at_sequence"]) == final_producer_sequence:
            return  # idempotent re-close
        raise SequencerError(
            f"producer {producer_id!r} is already closed at "
            f"{row['closed_at_sequence']}, cannot re-close at {final_producer_sequence}"
        )
    if int(row["admitted_through"]) != final_producer_sequence:
        raise SequencerError(
            f"producer {producer_id!r} claims to close at {final_producer_sequence} but only "
            f"{row['admitted_through']} records were admitted; the tail is incomplete"
        )

    conn.execute(
        """
        UPDATE producer_stream SET closed_at_sequence = %s, closed_at = %s
        WHERE run_id = %s AND attempt_id = %s AND producer_id = %s
        """,
        (final_producer_sequence, now or datetime.now(UTC), run_id, attempt_id, producer_id),
    )


def producer_tails_closed(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    attempt_id: str,
    required_producers: frozenset[str],
) -> bool:
    """Whether every required producer has closed.

    This is the input to ``EvidenceValidity.producer_tails_closed``. A required producer with no
    stream at all counts as not closed — absence is not completion, and a producer that never spoke
    is indistinguishable from one whose records were lost.
    """
    rows = conn.execute(
        """
        SELECT producer_id, closed_at_sequence
        FROM producer_stream
        WHERE run_id = %s AND attempt_id = %s
        """,
        (run_id, attempt_id),
    ).fetchall()
    closed = {str(r["producer_id"]) for r in rows if r["closed_at_sequence"] is not None}
    return required_producers <= closed


def chain_is_contiguous(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, attempt_id: str
) -> bool:
    """Whether the canonical sequence has no gaps.

    Necessary but **not** sufficient for completeness: a contiguous chain says the sequencer
    assigned consecutive positions, not that every producer finished. Both checks are required.
    """
    row = conn.execute(
        """
        SELECT count(*) AS n, coalesce(max(sequence), 0) AS hi, coalesce(min(sequence), 1) AS lo
        FROM canonical_event
        WHERE run_id = %s AND attempt_id = %s
        """,
        (run_id, attempt_id),
    ).fetchone()
    if row is None or int(row["n"]) == 0:
        return True
    return int(row["lo"]) == 1 and int(row["hi"]) == int(row["n"])
