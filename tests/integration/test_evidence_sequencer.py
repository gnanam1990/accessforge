"""The single trusted evidence sequencer, against real PostgreSQL.

Four identities must stay distinct, and most of these tests exist to prove they do not get
conflated: canonical sequence, producer sequence, source record id, and source record digest.

The case that matters most is the last one. A contiguous canonical chain is not completeness — a
producer can go silent mid-run and leave a chain that looks perfect. Only authenticated closing
watermarks establish that every required producer finished (INV-06).

Requirements: FR-006, FR-014, FR-015. Invariants: INV-06, INV-10.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    connect,
    migrate,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x8A))
WS_OTHER = str(uuid.UUID(int=0x8B))
MANIFEST = digest({"manifest": "seq"})
SOURCE_TIME = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
SUPERVISOR = "supervisor-1"
OBSERVER = "observer-1"


@pytest.fixture()
def attempt(test_database_url: str) -> Iterator[tuple[str, str, str]]:
    """A workspace, a RUNNING run and an open attempt."""
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "Seq"), (WS_OTHER, "Other")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))

    with workspace_connection(test_database_url, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=0)
    yield test_database_url, run_id, attempt_id


def _admit(
    conn: object,
    run_id: str,
    attempt_id: str,
    *,
    producer: str = SUPERVISOR,
    seq: int = 1,
    record_id: str | None = None,
    payload: Mapping[str, object] | None = None,
    event_type: str = "READER_OBSERVATION",
) -> sequencer.AdmittedEvent:
    return sequencer.admit_record(
        conn,  # type: ignore[arg-type]
        workspace_id=WS,
        run_id=run_id,
        attempt_id=attempt_id,
        lease_epoch=0,
        producer_id=producer,
        source_record_id=record_id or f"{producer}-{seq}",
        producer_sequence=seq,
        event_type=event_type,
        manifest_digest=MANIFEST,
        payload=dict(payload) if payload is not None else {"spoke": f"line {seq}"},
        source_time=SOURCE_TIME,
    )


# --- canonical ordering ------------------------------------------------------------------------


def test_the_chain_starts_at_one_and_from_the_documented_genesis(
    attempt: tuple[str, str, str],
) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        first = _admit(conn, run_id, attempt_id, seq=1)
    assert first.sequence == 1
    assert first.previous_event_hash == sequencer.GENESIS_HASH, (
        "a chain must not be silently re-rootable by inventing a different first hash"
    )


def test_sequences_are_assigned_consecutively_by_the_sequencer(
    attempt: tuple[str, str, str],
) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        positions = [_admit(conn, run_id, attempt_id, seq=n).sequence for n in (1, 2, 3)]
    assert positions == [1, 2, 3]
    with workspace_connection(url, WS) as conn:
        assert sequencer.chain_is_contiguous(conn, run_id=run_id, attempt_id=attempt_id)


def test_each_link_chains_to_the_previous(attempt: tuple[str, str, str]) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        one = _admit(conn, run_id, attempt_id, seq=1)
        two = _admit(conn, run_id, attempt_id, seq=2)
    assert two.previous_event_hash != one.previous_event_hash
    assert two.previous_event_hash != sequencer.GENESIS_HASH


def test_a_producer_does_not_choose_its_canonical_position(
    attempt: tuple[str, str, str],
) -> None:
    """Two producers interleave; canonical order is the sequencer's, not theirs.

    Both submit their own sequence 1, and they land at canonical 1 and 2. Conflating the two
    numbering schemes would make one producer's record overwrite the other's position.
    """
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        a = _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        b = _admit(conn, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="EFFECT_RECEIPT")
    assert (a.sequence, b.sequence) == (1, 2)


# --- replay versus conflict --------------------------------------------------------------------


def test_the_same_source_record_with_the_same_content_replays_idempotently(
    attempt: tuple[str, str, str],
) -> None:
    url, run_id, attempt_id = attempt
    payload = {"spoke": "the same thing"}
    with workspace_connection(url, WS) as conn:
        first = _admit(conn, run_id, attempt_id, seq=1, record_id="r1", payload=payload)
    with workspace_connection(url, WS) as conn:
        second = _admit(conn, run_id, attempt_id, seq=1, record_id="r1", payload=payload)

    assert not first.is_replay
    assert second.is_replay
    assert second.sequence == first.sequence, "a replay must not append a second event"

    with workspace_connection(url, WS) as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM canonical_event WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert count is not None and int(count["n"]) == 1


def test_the_same_source_record_with_different_content_is_a_conflict(
    attempt: tuple[str, str, str],
) -> None:
    """One of the two submissions is wrong, and the chain will not guess which."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, seq=1, record_id="r1", payload={"spoke": "a"})
    with workspace_connection(url, WS) as conn:
        with pytest.raises(sequencer.SourceRecordConflict):
            _admit(conn, run_id, attempt_id, seq=1, record_id="r1", payload={"spoke": "b"})


def test_a_producer_cannot_reuse_its_sequence_for_a_different_record(
    attempt: tuple[str, str, str],
) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, seq=1, record_id="r1")
    with workspace_connection(url, WS) as conn, pytest.raises(sequencer.SequencerError):
        # A different record id claiming an already-admitted producer sequence.
        _admit(conn, run_id, attempt_id, seq=1, record_id="r2")


# --- per-producer contiguity --------------------------------------------------------------------


def test_a_gap_in_a_producers_own_sequence_is_refused(attempt: tuple[str, str, str]) -> None:
    """Records are staged until contiguous.

    Admitting sequence 3 while 2 is still in flight would let the canonical chain look complete
    while a producer's middle is missing.
    """
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, seq=1)
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(sequencer.SequencerError, match="admitted through"),
    ):
        _admit(conn, run_id, attempt_id, seq=3)


def test_producers_advance_independently(attempt: tuple[str, str, str]) -> None:
    """One producer's position says nothing about another's."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=2)
        # The observer's first record is its sequence 1, not 3.
        _admit(conn, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="EFFECT_RECEIPT")


# --- closing watermarks: the missing-tail case --------------------------------------------------


def test_a_contiguous_chain_does_not_establish_completeness(
    attempt: tuple[str, str, str],
) -> None:
    """INV-06, stated as directly as it can be.

    The chain has no gaps. The supervisor closed. The observer never closed — it may have died
    mid-run. Completeness must be False, because a perfect-looking chain is exactly what a silent
    producer leaves behind.
    """
    url, run_id, attempt_id = attempt
    required = frozenset({SUPERVISOR, OBSERVER})

    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        _admit(conn, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="EFFECT_RECEIPT")
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )

    with workspace_connection(url, WS) as conn:
        assert sequencer.chain_is_contiguous(conn, run_id=run_id, attempt_id=attempt_id)
        assert not sequencer.producer_tails_closed(
            conn, run_id=run_id, attempt_id=attempt_id, required_producers=required
        )

    with workspace_connection(url, WS) as conn:
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=OBSERVER,
            final_producer_sequence=1,
        )
    with workspace_connection(url, WS) as conn:
        # Allowed-path control.
        assert sequencer.producer_tails_closed(
            conn, run_id=run_id, attempt_id=attempt_id, required_producers=required
        )


def test_a_required_producer_that_never_spoke_counts_as_not_closed(
    attempt: tuple[str, str, str],
) -> None:
    """Absence is not completion.

    A producer with no stream at all is indistinguishable from one whose records were lost, so it
    cannot be treated as having finished.
    """
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )
    with workspace_connection(url, WS) as conn:
        assert not sequencer.producer_tails_closed(
            conn,
            run_id=run_id,
            attempt_id=attempt_id,
            required_producers=frozenset({SUPERVISOR, "observer-that-never-ran"}),
        )


def test_a_watermark_beyond_what_was_admitted_is_refused(
    attempt: tuple[str, str, str],
) -> None:
    """A producer claiming records the sequencer never saw is claiming a tail that does not
    exist."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(sequencer.SequencerError, match="tail is incomplete"),
    ):
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=5,
        )


def test_a_record_after_the_watermark_is_refused(attempt: tuple[str, str, str]) -> None:
    """Closing means finished. A later record would mean the stream lied about being done."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(sequencer.SequencerError, match="closing watermark"),
    ):
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=2)


def test_re_closing_at_the_same_sequence_is_idempotent(attempt: tuple[str, str, str]) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )
        # A redelivered watermark must not be an error; delivery is at-least-once.
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )


# --- concurrency -------------------------------------------------------------------------------


def test_two_sequencers_for_one_attempt_are_serialized(attempt: tuple[str, str, str]) -> None:
    """The attempt-scoped advisory lock, exercised with two real connections.

    Without it both would read the same tail and assign the same canonical position. The unique
    constraint would catch the collision, but as a crash rather than as serialization.
    """
    url, run_id, attempt_id = attempt
    first = connect(url)
    second = connect(url)
    try:
        for conn in (first, second):
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, false)", (WS,))

        a = _admit(first, run_id, attempt_id, producer=SUPERVISOR, seq=1)
        # The second connection blocks on the advisory lock until the first commits, so it cannot
        # observe the pre-commit tail.
        first.commit()
        b = _admit(
            second, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="EFFECT_RECEIPT"
        )
        second.commit()
    finally:
        first.close()
        second.close()

    assert {a.sequence, b.sequence} == {1, 2}, "two records must not share a canonical position"


def test_distinct_attempts_do_not_contend(attempt: tuple[str, str, str]) -> None:
    """The lock is keyed on the attempt, so unrelated runs never block each other."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        other_run = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        other_attempt = runs.start_attempt(conn, run_id=other_run, workspace_id=WS, lease_epoch=0)

    first = connect(url)
    second = connect(url)
    try:
        for conn in (first, second):
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, false)", (WS,))
        a = _admit(first, run_id, attempt_id, seq=1)
        b = sequencer.admit_record(
            second,
            workspace_id=WS,
            run_id=other_run,
            attempt_id=other_attempt,
            lease_epoch=0,
            producer_id=SUPERVISOR,
            source_record_id="o-1",
            producer_sequence=1,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"spoke": "other run"},
            source_time=SOURCE_TIME,
        )
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()

    # Both are sequence 1: each attempt has its own chain.
    assert (a.sequence, b.sequence) == (1, 1)


def test_one_attempt_per_run_and_epoch(attempt: tuple[str, str, str]) -> None:
    """INV-10 in schema form: two actors cannot both believe they hold the same session."""
    import psycopg

    url, run_id, _ = attempt
    with pytest.raises(psycopg.errors.UniqueViolation):
        with workspace_connection(url, WS) as conn:
            runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=0)


# --- isolation ---------------------------------------------------------------------------------


def test_evidence_is_workspace_isolated(attempt: tuple[str, str, str]) -> None:
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        _admit(conn, run_id, attempt_id, seq=1)

    with workspace_connection(url, WS_OTHER) as conn:
        assert conn.execute("SELECT 1 FROM canonical_event").fetchall() == []
        assert conn.execute("SELECT 1 FROM producer_stream").fetchall() == []
        assert conn.execute("SELECT 1 FROM producer_source_record").fetchall() == []


def test_the_attempt_lock_is_actually_taken(attempt: tuple[str, str, str]) -> None:
    """Proof that the advisory lock does something, by observing it in pg_locks.

    An earlier version of this test raced two connections and expected a lock timeout. It passed
    with the advisory lock removed, because the second connection blocked on the canonical_event
    primary key instead — the unique index was doing the serializing, and the test could not tell
    the two mechanisms apart.

    Observing the lock directly removes the confound. The constraint would still catch a collision,
    but as a crash rather than as serialization, and the difference matters to anyone reading an
    incident log.
    """
    url, run_id, attempt_id = attempt

    # The key the sequencer derives from the attempt id.
    expected_key = int.from_bytes(
        hashlib.sha256(attempt_id.encode()).digest()[:8], "big", signed=True
    )

    conn = connect(url)
    try:
        conn.execute("SELECT set_config('accessforge.workspace_id', %s, false)", (WS,))
        before = conn.execute(
            "SELECT count(*) AS n FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
        ).fetchone()
        assert before is not None and int(before["n"]) == 0

        _admit(conn, run_id, attempt_id, seq=1)

        held = conn.execute(
            """
            SELECT ((classid::bigint << 32) | objid::bigint) AS key
            FROM pg_locks
            WHERE locktype = 'advisory' AND pid = pg_backend_pid()
            """
        ).fetchall()
        keys = {
            int(r["key"]) - (1 << 64) if int(r["key"]) >= (1 << 63) else int(r["key"]) for r in held
        }
        assert keys == {expected_key}, (
            f"expected one advisory lock on the attempt key {expected_key}, found {keys}"
        )
        conn.commit()
    finally:
        conn.close()

    # Transaction-scoped: the lock is gone once the transaction ends.
    with workspace_connection(url, WS) as check:
        rows = check.execute(
            "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'"
        ).fetchone()
    assert rows is not None and int(rows["n"]) == 0


def test_distinct_attempts_take_distinct_locks(attempt: tuple[str, str, str]) -> None:
    """The lock is keyed on the attempt, so unrelated runs never contend for it."""
    url, run_id, attempt_id = attempt
    with workspace_connection(url, WS) as conn:
        other_run = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        other_attempt = runs.start_attempt(conn, run_id=other_run, workspace_id=WS, lease_epoch=0)

    key_a = int.from_bytes(hashlib.sha256(attempt_id.encode()).digest()[:8], "big", signed=True)
    key_b = int.from_bytes(hashlib.sha256(other_attempt.encode()).digest()[:8], "big", signed=True)
    assert key_a != key_b


# --- independent review findings ---------------------------------------------------------------


def test_a_replay_reports_its_own_position_not_a_twin_with_the_same_payload(
    attempt: tuple[str, str, str],
) -> None:
    """Regression: the replay lookup keyed on content instead of provenance.

    Two identical reader observations are entirely ordinary — a reader saying "Loading" twice
    produces two distinct source records with the same payload. The replay query searched
    canonical_event by payload_digest and returned the FIRST match, so redelivering the second
    record reported the first record's canonical position and event id.

    CONTRACTS section 7 keys replay on producer plus source record id. Content is how a replay is
    distinguished from a conflict; it is not an identity.
    """
    url, run_id, attempt_id = attempt
    same = {"spoke": "Loading"}

    with workspace_connection(url, WS) as conn:
        first = _admit(conn, run_id, attempt_id, seq=1, record_id="A", payload=same)
        second = _admit(conn, run_id, attempt_id, seq=2, record_id="B", payload=same)

    assert (first.sequence, second.sequence) == (1, 2)
    assert first.event_id != second.event_id

    with workspace_connection(url, WS) as conn:
        replay_a = _admit(conn, run_id, attempt_id, seq=1, record_id="A", payload=same)
        replay_b = _admit(conn, run_id, attempt_id, seq=2, record_id="B", payload=same)

    assert replay_a.is_replay and replay_b.is_replay
    assert replay_a.sequence == 1 and replay_a.event_id == first.event_id
    assert replay_b.sequence == 2, (
        "a replay must report its own canonical position, not that of a twin with the same payload"
    )
    assert replay_b.event_id == second.event_id
    assert replay_b.previous_event_hash == second.previous_event_hash


def test_a_session_cannot_sequence_onto_another_workspaces_attempt(
    attempt: tuple[str, str, str],
) -> None:
    """Regression: a cross-tenant denial of service.

    `admit_record` trusted the caller's `workspace_id` and never checked it against the workspace
    that owns the run and attempt. Referential-integrity checks bypass row-level security, so a
    session scoped to workspace A could insert a row labelled A while chained onto workspace B's
    (run, attempt) sequence space.

    The consequence was worse than pollution. B could not see the injected row — its policy filters
    on workspace — but the row occupied canonical position 1, so B's own first record failed with a
    bare UniqueViolation on a chain that looked empty to it. Unexplainable from inside B.
    """
    url, _, _ = attempt
    with workspace_connection(url, WS_OTHER) as conn:
        victim_run = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
        victim_attempt = runs.start_attempt(
            conn, run_id=victim_run, workspace_id=WS_OTHER, lease_epoch=0
        )

    with workspace_connection(url, WS) as conn, pytest.raises(sequencer.SequencerError):
        sequencer.admit_record(
            conn,
            workspace_id=WS,  # the attacker's own workspace label
            run_id=victim_run,  # but another workspace's run
            attempt_id=victim_attempt,
            lease_epoch=0,
            producer_id="attacker",
            source_record_id="x1",
            producer_sequence=1,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"spoke": "injected"},
            source_time=SOURCE_TIME,
        )

    # The victim can still sequence its own evidence from position 1.
    with workspace_connection(url, WS_OTHER) as conn:
        legit = sequencer.admit_record(
            conn,
            workspace_id=WS_OTHER,
            run_id=victim_run,
            attempt_id=victim_attempt,
            lease_epoch=0,
            producer_id=SUPERVISOR,
            source_record_id="legit-1",
            producer_sequence=1,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"spoke": "its own evidence"},
            source_time=SOURCE_TIME,
        )
    assert legit.sequence == 1


def test_closing_a_stream_also_refuses_a_foreign_attempt(attempt: tuple[str, str, str]) -> None:
    url, _, _ = attempt
    with workspace_connection(url, WS_OTHER) as conn:
        victim_run = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
        victim_attempt = runs.start_attempt(
            conn, run_id=victim_run, workspace_id=WS_OTHER, lease_epoch=0
        )
    with workspace_connection(url, WS) as conn, pytest.raises(sequencer.SequencerError):
        sequencer.close_producer_stream(
            conn,
            workspace_id=WS,
            run_id=victim_run,
            attempt_id=victim_attempt,
            producer_id=SUPERVISOR,
            final_producer_sequence=1,
        )


def test_the_database_refuses_a_workspace_run_mismatch_even_without_the_application_check(
    attempt: tuple[str, str, str],
) -> None:
    """Defence in depth: composite foreign keys, so raw SQL cannot do it either.

    The application check is the clear error message. The constraint is what holds when a future
    caller, migration or report reaches the table directly.
    """
    import psycopg

    url, _, _ = attempt
    with workspace_connection(url, WS_OTHER) as conn:
        victim_run = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
        victim_attempt = runs.start_attempt(
            conn, run_id=victim_run, workspace_id=WS_OTHER, lease_epoch=0
        )

    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.InsufficientPrivilege)):
        with workspace_connection(url, WS) as conn:
            conn.execute(
                """
                INSERT INTO canonical_event
                    (workspace_id, run_id, attempt_id, sequence, event_id, event_type, lease_epoch,
                     source_time, manifest_digest, previous_event_hash, payload_digest, payload)
                VALUES (%s,%s,%s,1,%s,'READER_OBSERVATION',0,%s,%s,%s,%s,'{}')
                """,
                (
                    WS,
                    victim_run,
                    victim_attempt,
                    str(uuid.uuid4()),
                    SOURCE_TIME,
                    MANIFEST,
                    sequencer.GENESIS_HASH,
                    MANIFEST,
                ),
            )
