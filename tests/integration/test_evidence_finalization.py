"""Finalization prerequisites, late arrivals and bounded ingest.

The assertion this file exists for: **a contiguous canonical chain is not a complete evidence set.**
The sequencer assigns consecutive positions to whatever it admits, so a producer that stopped
halfway leaves a perfect chain and half the evidence. Contiguity and closing watermarks are
separate checks, and both are required (INV-06).

Requirements: FR-006, FR-014, FR-015. Invariants: INV-03, INV-06, INV-07, INV-11, INV-14, INV-15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from accessforge_domain import reducers
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    evidence,
    migrate,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xB0))
MANIFEST = digest({"m": "10-final"})
SUPERVISOR = "supervisor:mac-01"
OBSERVER = "observer:receipts"
REQUIRED = frozenset({SUPERVISOR, OBSERVER})
TRANSCRIPT = b'{"phrases": ["Email, invalid entry"]}'


@pytest.fixture(scope="session")
def store() -> evidence.S3ArtifactStore:
    endpoint = os.environ.get("OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.fail("OBJECT_STORE_ENDPOINT is not configured; there is no filesystem fallback")
    s3 = evidence.S3ArtifactStore(
        evidence.S3Settings(
            endpoint_url=endpoint,
            access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
            secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
            bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        )
    )
    s3.ensure_bucket()
    return s3


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (WS, "A"))
    yield test_database_url


def _attempt(url: str) -> tuple[str, str]:
    with workspace_connection(url, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
    return run_id, attempt_id


def _admit(
    url: str,
    run_id: str,
    attempt_id: str,
    *,
    producer_id: str,
    producer_sequence: int,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> None:
    with workspace_connection(url, WS) as conn:
        sequencer.admit_record(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            lease_epoch=1,
            producer_id=producer_id,
            source_record_id=f"{producer_id}:{producer_sequence}",
            producer_sequence=producer_sequence,
            event_type=event_type,
            manifest_digest=MANIFEST,
            payload=payload or {"n": producer_sequence},
            source_time=datetime(2026, 9, 10, 12, producer_sequence % 60, tzinfo=UTC),
        )


def _close(url: str, run_id: str, attempt_id: str, producer_id: str, final: int) -> None:
    with workspace_connection(url, WS) as conn:
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=producer_id,
            final_producer_sequence=final,
        )


def _complete_attempt(url: str, s3: evidence.S3ArtifactStore) -> tuple[str, str]:
    """A fully complete attempt: bounded lifecycle, both producers closed, artifact promoted."""
    run_id, attempt_id = _attempt(url)
    with workspace_connection(url, WS) as conn:
        evidence.declare_required_artifacts(
            conn, workspace_id=WS, run_id=run_id, requirements={"SPEECH_TRANSCRIPT": SUPERVISOR}
        )
    _admit(
        url,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=1,
        event_type="RUN_STARTED",
    )
    _admit(
        url,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=2,
        event_type="READER_OBSERVATION",
    )
    _admit(
        url,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=3,
        event_type="RUN_FINISHED",
    )
    _admit(
        url,
        run_id,
        attempt_id,
        producer_id=OBSERVER,
        producer_sequence=1,
        event_type="EFFECT_RECEIPT",
    )
    _close(url, run_id, attempt_id, SUPERVISOR, 3)
    _close(url, run_id, attempt_id, OBSERVER, 1)

    with workspace_connection(url, WS) as conn:
        artifact = evidence.upload_to_quarantine(
            conn,
            s3,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            kind="SPEECH_TRANSCRIPT",
            producer_id=SUPERVISOR,
            lease_epoch=1,
            manifest_digest=MANIFEST,
            content_type="application/json",
            payload=TRANSCRIPT,
        )
        evidence.promote(
            conn, s3, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
        )
    return run_id, attempt_id


def _assess(
    url: str, s3: evidence.S3ArtifactStore, run_id: str, attempt_id: str
) -> evidence.Completeness:
    with workspace_connection(url, WS) as conn:
        return evidence.assess_completeness(
            conn, s3, run_id=run_id, attempt_id=attempt_id, required_producers=REQUIRED
        )


# --- the allowed path ----------------------------------------------------------------------------


def test_a_complete_attempt_is_complete(db: str, store: evidence.S3ArtifactStore) -> None:
    """The control. Every refusal below would be meaningless against a checker that refuses all."""
    run_id, attempt_id = _complete_attempt(db, store)
    result = _assess(db, store, run_id, attempt_id)
    assert result.complete, result.reasons
    assert result.reasons == []


def test_reasons_are_empty_exactly_when_complete(db: str, store: evidence.S3ArtifactStore) -> None:
    """Asserted rather than trusted: the two could drift apart, and callers read the boolean."""
    run_id, attempt_id = _complete_attempt(db, store)
    complete = _assess(db, store, run_id, attempt_id)
    assert complete.complete is (complete.reasons == [])

    incomplete_run, incomplete_attempt = _attempt(db)
    incomplete = _assess(db, store, incomplete_run, incomplete_attempt)
    assert incomplete.complete is (incomplete.reasons == [])


def test_completeness_cannot_express_an_outcome(db: str, store: evidence.S3ArtifactStore) -> None:
    """Structural. A completeness checker that could return PASS would be a second place a verdict
    is decided, and the architecture depends on there being exactly one."""
    fields = set(evidence.Completeness.__dataclass_fields__)
    assert not any(f in fields for f in ("outcome", "verdict", "passed", "result", "status"))


# --- a contiguous chain is not a complete evidence set -------------------------------------------


def test_a_contiguous_chain_with_an_unclosed_producer_is_incomplete(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The central case. Every event the sequencer saw is present and consecutive, and the observer
    simply stopped sending. The chain is perfect and half the evidence is missing."""
    run_id, attempt_id = _attempt(db)
    with workspace_connection(db, WS) as conn:
        evidence.declare_required_artifacts(conn, workspace_id=WS, run_id=run_id, requirements={})
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=1,
        event_type="RUN_STARTED",
    )
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=2,
        event_type="RUN_FINISHED",
    )
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=OBSERVER,
        producer_sequence=1,
        event_type="EFFECT_RECEIPT",
    )
    _close(db, run_id, attempt_id, SUPERVISOR, 2)
    # The observer never closes.

    result = _assess(db, store, run_id, attempt_id)
    assert result.chain_contiguous is True, "the chain itself has no gaps"
    assert result.producers_closed is False
    assert not result.complete
    assert any(OBSERVER in r for r in result.reasons)
    assert any("contiguous chain does not cover this" in r for r in result.reasons)


def test_a_required_producer_that_never_spoke_is_unclosed_rather_than_absent(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Absence is not completion. A producer that never spoke is indistinguishable from one whose
    records were all lost."""
    run_id, attempt_id = _attempt(db)
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=1,
        event_type="RUN_STARTED",
    )
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=2,
        event_type="RUN_FINISHED",
    )
    _close(db, run_id, attempt_id, SUPERVISOR, 2)

    result = _assess(db, store, run_id, attempt_id)
    assert not result.complete
    assert any(OBSERVER in r for r in result.reasons)


def test_an_unbounded_lifecycle_is_incomplete(db: str, store: evidence.S3ArtifactStore) -> None:
    """Without a start and a finish the attempt's extent is undefined, so nothing says the evidence
    covers the whole of it."""
    run_id, attempt_id = _attempt(db)
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=SUPERVISOR,
        producer_sequence=1,
        event_type="READER_OBSERVATION",
    )
    _admit(
        db,
        run_id,
        attempt_id,
        producer_id=OBSERVER,
        producer_sequence=1,
        event_type="EFFECT_RECEIPT",
    )
    _close(db, run_id, attempt_id, SUPERVISOR, 1)
    _close(db, run_id, attempt_id, OBSERVER, 1)

    result = _assess(db, store, run_id, attempt_id)
    assert result.lifecycle_bounded is False
    assert any("extent is undefined" in r for r in result.reasons)


def test_every_failing_check_is_reported_not_just_the_first(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """An operator who fixes one thing and re-runs to find the next is being made to work through a
    list one item at a time."""
    run_id, attempt_id = _attempt(db)
    with workspace_connection(db, WS) as conn:
        evidence.declare_required_artifacts(
            conn, workspace_id=WS, run_id=run_id, requirements={"SPEECH_TRANSCRIPT": SUPERVISOR}
        )
    result = _assess(db, store, run_id, attempt_id)
    assert len(result.reasons) >= 3, result.reasons
    assert any("producers have not closed" in r for r in result.reasons)
    assert any("required artifacts are missing" in r for r in result.reasons)
    assert any("extent is undefined" in r for r in result.reasons)


def test_a_swapped_artifact_makes_a_complete_attempt_incomplete(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Integrity is re-checked at finalization, not only at promotion. An object replaced after
    promotion would otherwise carry a promoted row and a digest of bytes that are gone."""
    run_id, attempt_id = _complete_attempt(db, store)
    assert _assess(db, store, run_id, attempt_id).complete

    with workspace_connection(db, WS) as conn:
        artifact = conn.execute(
            "SELECT object_key FROM evidence_artifact WHERE attempt_id = %s", (attempt_id,)
        ).fetchone()
        assert artifact is not None
        key = str(artifact["object_key"])
    store.put(key=key, payload=b'{"phrases": []}', content_type="application/json")

    result = _assess(db, store, run_id, attempt_id)
    assert not result.complete
    assert any("do not match their recorded digests" in r for r in result.reasons)


# --- late and stale arrivals ----------------------------------------------------------------------


def test_a_late_run_finished_cannot_resurrect_an_interrupted_run(db: str) -> None:
    """Named in the module prompt. A supervisor partitioned during an interruption reconnects and
    sends its buffered tail; admitting it adds evidence from after the run was declared over."""
    run_id, attempt_id = _attempt(db)
    with workspace_connection(db, WS) as conn:
        state = runs.load_run(conn, run_id=run_id).state
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.interrupt(
                s, reason="ACTION_RESULT_NEVER_ARRIVED", expected_revision=s.revision
            ),
            operation_id=str(uuid.uuid4()),
            topic="run.interrupted",
            expected_revision=state.revision,
            actor_service="test",
        )

    with workspace_connection(db, WS) as conn:
        with pytest.raises(evidence.FinalizationError, match="INTERRUPTED"):
            evidence.assert_run_accepts_evidence(
                conn,
                workspace_id=WS,
                run_id=run_id,
                producer_id=SUPERVISOR,
                event_type="RUN_FINISHED",
            )
        row = conn.execute(
            "SELECT reason_code, event_type, reason_detail FROM rejected_arrival WHERE run_id = %s",
            (run_id,),
        ).fetchone()

    assert row is not None, "the rejection is recorded, not dropped"
    assert str(row["reason_code"]) == "RUN_ALREADY_TERMINAL"
    assert str(row["event_type"]) == "RUN_FINISHED"
    assert "declared over" in str(row["reason_detail"])


def test_a_nonterminal_run_still_accepts_evidence(db: str) -> None:
    """Allowed-path control for the gate above."""
    run_id, _ = _attempt(db)
    with workspace_connection(db, WS) as conn:
        evidence.assert_run_accepts_evidence(
            conn,
            workspace_id=WS,
            run_id=run_id,
            producer_id=SUPERVISOR,
            event_type="READER_OBSERVATION",
        )


def test_a_rejected_arrival_records_no_payload(db: str) -> None:
    """Structural. A rejected record may carry a transcript, a form value or a token, and storing it
    to be helpful would move unvalidated content from an unauthenticated source into the audit
    trail."""
    import inspect

    params = set(inspect.signature(evidence.record_rejected_arrival).parameters)
    assert "payload" not in params
    assert "payload_digest" in params, "the digest correlates without holding the content"

    with unscoped_connection(os.environ["TEST_DATABASE_URL"]) as conn:
        columns = {
            str(r["column_name"])
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'rejected_arrival'"
            ).fetchall()
        }
    assert "payload" not in columns


# --- bounded ingest -------------------------------------------------------------------------------


def test_staging_capacity_is_bounded_and_the_refusal_is_recorded(db: str) -> None:
    """INV-14. An unbounded staging buffer is a memory-exhaustion channel: a producer that sends
    sequence 2, 3, 4 forever and never sends 1 stages every one of them."""
    run_id, attempt_id = _attempt(db)
    with workspace_connection(db, WS) as conn:
        assert (
            evidence.assert_staging_capacity(
                conn,
                workspace_id=WS,
                run_id=run_id,
                attempt_id=attempt_id,
                producer_id=SUPERVISOR,
                limit=10,
            )
            == 0
        )
        with pytest.raises(evidence.FinalizationError, match="pushing back"):
            evidence.assert_staging_capacity(
                conn,
                workspace_id=WS,
                run_id=run_id,
                attempt_id=attempt_id,
                producer_id=SUPERVISOR,
                limit=0,
            )
        row = conn.execute(
            "SELECT reason_code, reason_detail FROM rejected_arrival WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row is not None
    assert str(row["reason_code"]) == "BUFFER_FULL"
    assert "refused rather than an older one evicted" in str(row["reason_detail"])


# --- summaries and replay ------------------------------------------------------------------------


def test_the_summary_carries_counts_and_identities_but_no_content(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """A summary is the thing most likely to be rendered somewhere unexpected. An object key in a
    summary is a step towards an object key in a log."""
    run_id, attempt_id = _complete_attempt(db, store)
    with workspace_connection(db, WS) as conn:
        summary = evidence.evidence_summary(conn, run_id=run_id, attempt_id=attempt_id)

    assert summary["events"] == 4
    assert {p["producerId"] for p in summary["producers"]} == REQUIRED
    assert summary["artifacts"][0]["kind"] == "SPEECH_TRANSCRIPT"

    rendered = repr(summary)
    assert "workspaces/" not in rendered, "no object keys"
    assert "Email, invalid entry" not in rendered, "no transcript content"


def test_replay_is_ordered_and_paginates_by_keyset(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Keyset rather than OFFSET. The chain is append-only, so a keyset cursor is stable under
    concurrent ingestion; an OFFSET page would silently skip or repeat records as the chain grew."""
    run_id, attempt_id = _complete_attempt(db, store)
    with workspace_connection(db, WS) as conn:
        first = evidence.replay(conn, run_id=run_id, attempt_id=attempt_id, limit=2)
        second = evidence.replay(
            conn,
            run_id=run_id,
            attempt_id=attempt_id,
            after_sequence=first["nextAfterSequence"],
            limit=2,
        )

    sequences = [e["sequence"] for e in first["events"]] + [e["sequence"] for e in second["events"]]
    assert sequences == [1, 2, 3, 4]

    # Neither page reports exhaustion, and that is correct rather than a bug. Both are exactly full,
    # and a full page cannot distinguish "four events exist" from "four events exist so far". The
    # end is confirmed by fetching once more and getting nothing. Reporting exhaustion on a full
    # page would stop a reader one page early whenever the total is a multiple of the page size.
    assert first["exhausted"] is False
    assert second["exhausted"] is False

    with workspace_connection(db, WS) as conn:
        third = evidence.replay(
            conn,
            run_id=run_id,
            attempt_id=attempt_id,
            after_sequence=second["nextAfterSequence"],
            limit=2,
        )
    assert third["events"] == []
    assert third["exhausted"] is True


def test_a_replay_page_size_outside_the_bounds_is_refused(db: str) -> None:
    run_id, attempt_id = _attempt(db)
    with workspace_connection(db, WS) as conn:
        for bad in (0, -1, 1001):
            with pytest.raises(evidence.FinalizationError, match="page size"):
                evidence.replay(conn, run_id=run_id, attempt_id=attempt_id, limit=bad)
