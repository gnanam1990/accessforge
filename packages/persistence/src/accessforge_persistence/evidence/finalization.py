"""Finalization prerequisites, late arrivals, and bounded ingest.

This module answers one question — *is this attempt's evidence complete enough to evaluate?* — and
deliberately does not answer the next one. It returns reasons, never an outcome. Module 11 owns the
verdict, and a completeness checker that guessed at outcomes would be a second place where PASS
could be decided.

The distinction that took module 04 a defect to learn, restated here because module 10 is where it
becomes load-bearing: **a contiguous canonical chain is not a complete evidence set.** The sequencer
assigns consecutive positions to whatever it admits. If a producer stopped sending halfway through,
the chain is perfectly contiguous and half the evidence is missing. Contiguity and closing
watermarks are separate checks and both are required (INV-06).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.states import RunStatus
from accessforge_domain.timestamps import to_rfc3339_utc

from . import artifacts
from .objectstore import ArtifactStore

#: How many records may be staged per attempt awaiting contiguity before ingest pushes back.
#:
#: Bounded because an unbounded staging buffer is a memory-exhaustion channel: a producer that sends
#: sequence 2, 3, 4 … forever and never sends 1 stages every one of them. INV-14 requires the
#: exhaustion to be visible, and the module prompt requires that it "cannot discard required
#: evidence while preserving a green result" -- so hitting the limit rejects the *new* arrival and
#: records why, rather than evicting something already staged.
MAX_STAGED_RECORDS_PER_ATTEMPT = 1000


class FinalizationError(Exception):
    """A finalization query was refused."""


@dataclass(frozen=True, slots=True)
class Completeness:
    """Why an attempt is or is not ready to be evaluated.

    ``reasons`` is empty exactly when ``complete`` is true, and a test asserts that rather than
    trusting it. There is no outcome field: this type cannot express PASS.
    """

    complete: bool
    reasons: list[str] = field(default_factory=list)
    chain_contiguous: bool = False
    producers_closed: bool = False
    artifacts_present: bool = False
    lifecycle_bounded: bool = False


def _now(now: str | None) -> str:
    return now or to_rfc3339_utc(datetime.now(UTC))


def assess_completeness(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    run_id: str,
    attempt_id: str,
    required_producers: frozenset[str],
) -> Completeness:
    """Assemble every finalization prerequisite and report what is missing.

    Every check runs, and every failure is reported. Short-circuiting on the first would make an
    operator fix one thing, re-run, and discover the next -- and the module prompt asks for
    "invalid/incomplete reasons", plural.
    """
    from accessforge_persistence import sequencer

    reasons: list[str] = []

    contiguous = sequencer.chain_is_contiguous(conn, run_id=run_id, attempt_id=attempt_id)
    if not contiguous:
        reasons.append(
            "the canonical event chain has gaps: the sequencer admitted records at "
            "non-consecutive positions, so events are missing from the middle of the attempt"
        )

    closed = sequencer.producer_tails_closed(
        conn, run_id=run_id, attempt_id=attempt_id, required_producers=required_producers
    )
    if not closed:
        open_streams = _unclosed_producers(conn, attempt_id=attempt_id, required=required_producers)
        reasons.append(
            f"required producers have not closed their streams: {', '.join(open_streams)}. A "
            "contiguous chain does not cover this: the sequencer assigns consecutive positions to "
            "whatever it admits, so a producer that stopped halfway leaves a perfect chain and "
            "half the evidence (INV-06)."
        )

    missing = artifacts.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id)
    if missing:
        rendered = ", ".join(f"{m['kind']} from {m['producer']} ({m['reason']})" for m in missing)
        reasons.append(f"required artifacts are missing: {rendered}")

    lifecycle = _lifecycle_bounded(conn, run_id=run_id, attempt_id=attempt_id)
    if not lifecycle:
        reasons.append(
            "the attempt has no RUN_STARTED and RUN_FINISHED pair, so its extent is undefined and "
            "there is nothing to say the evidence covers the whole of it"
        )

    integrity = artifacts.verify_stored_integrity(conn, store, attempt_id=attempt_id)
    if integrity:
        reasons.append(
            "stored artifacts do not match their recorded digests: "
            + "; ".join(f"{p['artifact']} {p['problem']}" for p in integrity)
        )

    return Completeness(
        complete=not reasons,
        reasons=reasons,
        chain_contiguous=contiguous,
        producers_closed=closed,
        artifacts_present=not missing,
        lifecycle_bounded=lifecycle,
    )


def _unclosed_producers(
    conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str, required: frozenset[str]
) -> list[str]:
    rows = conn.execute(
        "SELECT producer_id, closed_at_sequence FROM producer_stream WHERE attempt_id = %s",
        (attempt_id,),
    ).fetchall()
    closed = {str(r["producer_id"]) for r in rows if r["closed_at_sequence"] is not None}
    # A required producer with no stream row at all is unclosed, not absent-and-therefore-fine.
    # Absence is not completion: a producer that never spoke is indistinguishable from one whose
    # records were all lost.
    return sorted(required - closed)


def _lifecycle_bounded(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, attempt_id: str
) -> bool:
    row = conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE event_type = 'RUN_STARTED')  AS started,
            count(*) FILTER (WHERE event_type = 'RUN_FINISHED') AS finished
        FROM canonical_event WHERE run_id = %s AND attempt_id = %s
        """,
        (run_id, attempt_id),
    ).fetchone()
    return row is not None and int(row["started"]) >= 1 and int(row["finished"]) >= 1


def record_rejected_arrival(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    producer_id: str,
    reason_code: str,
    reason_detail: str,
    attempt_id: str | None = None,
    source_record_id: str | None = None,
    event_type: str | None = None,
    payload_digest: str | None = None,
    now: str | None = None,
) -> str:
    """Record that something arrived and was refused.

    The payload is deliberately absent from the parameters. A rejected record may carry a
    transcript, a form value or a token, and storing it "so we can look at it later" moves
    unvalidated content from an unauthenticated source into the audit trail. The digest is enough
    to correlate it with a producer's own logs without holding the content.
    """
    arrival_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO rejected_arrival
            (id, workspace_id, run_id, attempt_id, producer_id, source_record_id, event_type,
             payload_digest, reason_code, reason_detail, arrived_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            arrival_id,
            workspace_id,
            run_id,
            attempt_id,
            producer_id,
            source_record_id,
            event_type,
            payload_digest,
            reason_code,
            reason_detail,
            _now(now),
        ),
    )
    return arrival_id


def assert_run_accepts_evidence(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    producer_id: str,
    event_type: str,
    now: str | None = None,
) -> None:
    """Refuse evidence for a run that has already ended, and record the attempt.

    The case the module prompt names: "a late RUN_FINISHED cannot resurrect an interrupted run."
    Terminal records are immutable (INV-11), and the failure this prevents is subtle -- a supervisor
    that was partitioned during an interruption reconnects, sends its buffered tail, and the run's
    evidence set silently gains records from after it was declared over.

    The rejection is recorded rather than dropped, because "the runner kept talking after we fenced
    it" is exactly what an operator investigating an ambiguous interruption needs to see.
    """
    row = conn.execute("SELECT status FROM run WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise FinalizationError("no such run in this workspace")

    status = RunStatus(str(row["status"]))
    if status in {RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}:
        record_rejected_arrival(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            producer_id=producer_id,
            event_type=event_type,
            reason_code="RUN_ALREADY_TERMINAL",
            reason_detail=(
                f"the run is {status} and terminal records are immutable. A {event_type} arriving "
                "now cannot change what was recorded, and admitting it would add evidence from "
                "after the run was declared over."
            ),
            now=now,
        )
        raise FinalizationError(
            f"run {run_id} is {status}; evidence is not admitted after a terminal record (INV-11)"
        )


def assert_staging_capacity(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    producer_id: str,
    limit: int = MAX_STAGED_RECORDS_PER_ATTEMPT,
    now: str | None = None,
) -> int:
    """Refuse a new arrival once staging is full, rather than evicting what is already staged.

    The direction matters. Evicting staged records to make room would discard evidence that has
    already been accepted, and the module prompt forbids exactly that: resource exhaustion "cannot
    discard required evidence while preserving a green result". Refusing the new arrival keeps the
    loss on the side that has not been admitted yet, and the refusal is recorded so the gap is
    visible rather than inferred.
    """
    row = conn.execute(
        """
        SELECT count(*) AS n FROM producer_source_record
        WHERE attempt_id = %s AND canonical_sequence IS NULL
        """,
        (attempt_id,),
    ).fetchone()
    staged = int(row["n"]) if row else 0
    if staged >= limit:
        record_rejected_arrival(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=producer_id,
            reason_code="BUFFER_FULL",
            reason_detail=(
                f"{staged} records are staged awaiting contiguity and the limit is {limit}. A "
                "producer whose earlier records never arrived stages every later one, so the "
                "buffer is bounded. The new arrival is refused rather than an older one evicted: "
                "evicting would discard evidence already accepted."
            ),
            now=now,
        )
        raise FinalizationError(
            f"{staged} records staged for this attempt, limit {limit}; ingest is pushing back"
        )
    return staged


def evidence_summary(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, attempt_id: str
) -> dict[str, Any]:
    """A bounded summary of an attempt's evidence, safe to show in a list view.

    Counts and identities only. No transcript text, no form values, no object keys -- an object key
    in a summary is a step towards an object key in a log, and the summary is the thing most likely
    to be rendered somewhere unexpected.
    """
    chain = conn.execute(
        """
        SELECT count(*) AS events, coalesce(max(sequence), 0) AS last_sequence
        FROM canonical_event WHERE run_id = %s AND attempt_id = %s
        """,
        (run_id, attempt_id),
    ).fetchone()
    producers = conn.execute(
        """
        SELECT producer_id, admitted_through, closed_at_sequence
        FROM producer_stream WHERE attempt_id = %s ORDER BY producer_id
        """,
        (attempt_id,),
    ).fetchall()
    artifact_rows = conn.execute(
        """
        SELECT kind, state, retention, content_digest, size_bytes
        FROM evidence_artifact WHERE attempt_id = %s ORDER BY kind, content_digest
        """,
        (attempt_id,),
    ).fetchall()
    rejected = conn.execute(
        "SELECT reason_code, count(*) AS n FROM rejected_arrival WHERE run_id = %s "
        "GROUP BY reason_code ORDER BY reason_code",
        (run_id,),
    ).fetchall()

    return {
        "events": int(chain["events"]) if chain else 0,
        "lastSequence": int(chain["last_sequence"]) if chain else 0,
        "producers": [
            {
                "producerId": str(r["producer_id"]),
                "admittedThrough": int(r["admitted_through"]),
                "closedAt": None
                if r["closed_at_sequence"] is None
                else int(r["closed_at_sequence"]),
            }
            for r in producers
        ],
        "artifacts": [
            {
                "kind": str(r["kind"]),
                "state": str(r["state"]),
                "retention": str(r["retention"]),
                "digest": str(r["content_digest"]),
                "sizeBytes": int(r["size_bytes"]),
            }
            for r in artifact_rows
        ],
        "rejectedArrivals": {str(r["reason_code"]): int(r["n"]) for r in rejected},
    }


def replay(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    attempt_id: str,
    after_sequence: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Paginated canonical replay, ordered by canonical sequence.

    Keyset pagination on the sequence rather than OFFSET. The chain is append-only, so a keyset
    cursor is stable under concurrent ingestion; an OFFSET page would silently skip or repeat
    records as the chain grew underneath a reader, and a replay that quietly omits an event is the
    one thing a replay must never do.
    """
    if limit < 1 or limit > 1000:
        raise FinalizationError("replay page size must be between 1 and 1000")

    rows = conn.execute(
        """
        SELECT sequence, event_id, event_type, source_time, received_time, previous_event_hash,
               payload_digest
        FROM canonical_event
        WHERE run_id = %s AND attempt_id = %s AND sequence > %s
        ORDER BY sequence
        LIMIT %s
        """,
        (run_id, attempt_id, after_sequence, limit),
    ).fetchall()

    # `exhausted` is true only for a *short* page. A page that is exactly full never sets it, even
    # when it happens to be the last one, because the query cannot distinguish "four events exist"
    # from "four events exist so far". A caller confirms the end by fetching once more and getting
    # nothing. Reporting exhaustion on a full page would be the more comfortable lie and would stop
    # a reader one page early whenever the total is a multiple of the page size.
    return {
        "events": [dict(r) for r in rows],
        "nextAfterSequence": int(rows[-1]["sequence"]) if rows else after_sequence,
        "exhausted": len(rows) < limit,
    }
