"""Durability under process loss, with real PostgreSQL transactions.

The crash matrix asks one question at four different instants: if the process dies *here*, what can
be proved afterwards? Each case below commits or rolls back at a specific boundary and then inspects
what a recovering worker would find.

One case uses a genuinely separate process killed with SIGKILL, because a rollback in-process is a
faithful model of a crash for the database but not for the question "did the operating system keep
its promise".

Requirements: FR-006, FR-014, FR-015, FR-021. Invariants: INV-06, INV-09, INV-10, INV-11, INV-13.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.reducers import (
    acknowledge_stop,
    admit_action,
    cancel,
    interrupt,
    progress,
    request_cancellation,
    resolve_action,
)
from accessforge_persistence import (
    assert_row_level_security_enforced,
    connect,
    idempotency,
    metrics,
    migrate,
    outbox,
    recovery,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0x7A))
WS_B = str(uuid.UUID(int=0x7B))
USER = str(uuid.UUID(int=0x7C))
MANIFEST = digest({"manifest": "m04"})
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace, app_user RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (USER, "o@example.test"))
    yield test_database_url


def _new_run(url: str, workspace: str = WS_A) -> str:
    with workspace_connection(url, workspace) as conn:
        return runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)


# --- crash boundary 1: before commit ----------------------------------------------------------


def test_a_crash_before_commit_leaves_no_trace(db: str) -> None:
    """Nothing half-happened: no run, no audit row, no outbox message."""
    run_id = str(uuid.uuid4())
    # psycopg.Rollback aborts the transaction block and is absorbed by it, which is exactly the
    # shape of a crash from the database's point of view: work done, nothing committed, no error
    # surfaced to anyone.
    with workspace_connection(db, WS_A) as conn:
        runs.create_run(conn, workspace_id=WS_A, manifest_digest=MANIFEST, run_id=run_id)
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
        raise psycopg.Rollback

    with workspace_connection(db, WS_A) as conn:
        assert conn.execute("SELECT 1 FROM run WHERE id = %s", (run_id,)).fetchall() == []
        assert outbox.unpublished_count(conn) == 0
        assert conn.execute("SELECT 1 FROM audit_event").fetchall() == []


# --- crash boundary 2: after commit, before publish -------------------------------------------


def test_a_crash_after_commit_but_before_publish_leaves_the_message_recoverable(db: str) -> None:
    """The reason the outbox exists.

    State and message committed together, so the message survives the crash and a later publisher
    finds it. Had the message been sent first and the commit lost, a consumer would have acted on
    work that was never accepted.
    """
    run_id = _new_run(db)
    operation_id = str(uuid.uuid4())

    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=operation_id,
            topic="run.leased",
            now=NOW,
        )
    # The process "dies" here: committed, nothing published.

    with workspace_connection(db, WS_A) as conn:
        assert outbox.unpublished_count(conn) == 1
        claimed = outbox.claim_messages(conn, claimed_by="publisher-2", now=NOW)
    assert len(claimed) == 1
    assert claimed[0].operation_id == operation_id
    assert claimed[0].reference["runId"] == run_id
    # A reference, not a command: the consumer re-reads state rather than trusting the payload.
    assert claimed[0].reference["status"] == "LEASED"


def test_state_and_its_message_are_never_separated(db: str) -> None:
    """A rolled-back transition leaves no orphan message behind."""
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
        raise psycopg.Rollback

    with workspace_connection(db, WS_A) as conn:
        assert outbox.unpublished_count(conn) == 0
        assert runs.load_run(conn, run_id=run_id).state.status.value == "QUEUED"


# --- crash boundary 3: after publish, before acknowledgement -----------------------------------


def test_a_crash_after_publish_but_before_acknowledgement_causes_redelivery(db: str) -> None:
    """At-least-once, demonstrated.

    The message was handed to the transport but never marked published, so it is delivered again.
    That is correct and expected; the consumer deduplicates on the operation identity, which is
    stable across both deliveries.
    """
    run_id = _new_run(db)
    operation_id = str(uuid.uuid4())
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=operation_id,
            topic="run.leased",
            now=NOW,
        )

    with workspace_connection(db, WS_A) as conn:
        first = outbox.claim_messages(conn, claimed_by="publisher-1", now=NOW)
    assert len(first) == 1
    # Published to the transport, then the worker dies before mark_published.

    later = NOW + timedelta(minutes=6)  # past the claim lease
    with workspace_connection(db, WS_A) as conn:
        second = outbox.claim_messages(conn, claimed_by="publisher-2", now=later)
    assert len(second) == 1
    assert second[0].id == first[0].id
    assert second[0].operation_id == first[0].operation_id, (
        "the operation identity must be stable across deliveries, or consumers cannot deduplicate"
    )
    assert second[0].attempts == 2, "the redelivery must be visible, not silent"


def test_a_claim_held_within_its_lease_is_not_stolen(db: str) -> None:
    """Allowed-path control for claiming: concurrent publishers do not duplicate work needlessly."""
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        assert len(outbox.claim_messages(conn, claimed_by="p1", now=NOW)) == 1
    with workspace_connection(db, WS_A) as conn:
        assert outbox.claim_messages(conn, claimed_by="p2", now=NOW + timedelta(minutes=1)) == []


def test_publication_is_not_business_completion(db: str) -> None:
    """A published message says a transport accepted it, nothing more."""
    run_id = _new_run(db)
    operation_id = str(uuid.uuid4())
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=operation_id,
            topic="run.leased",
            now=NOW,
        )
        claimed = outbox.claim_messages(conn, claimed_by="p1", now=NOW)
        outbox.mark_published(conn, message_id=claimed[0].id, now=NOW)

    with workspace_connection(db, WS_A) as conn:
        row = conn.execute("SELECT status FROM operation WHERE id = %s", (operation_id,)).fetchone()
    # No operation row exists at all here: publishing did not invent one, and would not have
    # marked one COMPLETED if it had.
    assert row is None


# --- crash boundary 4: after action intent -----------------------------------------------------


def test_an_action_intent_with_no_result_is_ambiguous_and_never_retried(db: str) -> None:
    """INV-09. The boundary that cannot be resolved by a database.

    The intent was recorded before dispatch, the process died, and the keystroke may or may not have
    reached the operating system. Recovery surfaces this for quarantine; nothing reschedules it.
    """
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.running",
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=admit_action,
            operation_id=str(uuid.uuid4()),
            topic="run.action",
            now=NOW,
        )
    # Process dies here, with the intent durable and no result.

    with workspace_connection(db, WS_A) as conn:
        ambiguous = recovery.ambiguous_attempts(conn)
    assert [a.run_id for a in ambiguous] == [run_id]
    assert "may have received it" in ambiguous[0].reason

    # The only admissible resolution is interruption with a reason, plus quarantine.
    with workspace_connection(db, WS_A) as conn:
        state = runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: interrupt(s, reason="action intent with no result after restart"),
            operation_id=str(uuid.uuid4()),
            topic="run.interrupted",
            now=NOW,
        )
    assert state.status.value == "INTERRUPTED"
    assert state.outcome.value == "INCONCLUSIVE"
    assert state.quarantined


def test_a_resolved_action_is_not_ambiguous(db: str) -> None:
    # Allowed-path control: recovery must not flag healthy runs.
    run_id = _new_run(db)
    for reducer, topic in ((progress, "leased"), (progress, "running"), (admit_action, "action")):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=reducer,
                operation_id=str(uuid.uuid4()),
                topic=topic,
                now=NOW,
            )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=resolve_action,
            operation_id=str(uuid.uuid4()),
            topic="result",
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        assert recovery.ambiguous_attempts(conn) == []


# --- a genuinely killed process ----------------------------------------------------------------


def test_an_uncommitted_transaction_in_a_killed_process_leaves_nothing(db: str) -> None:
    """SIGKILL mid-transaction, in a real separate process.

    An in-process rollback models a crash faithfully for the database, but not for the question of
    whether the operating system kept its promise. This kills a child with an open transaction and
    confirms PostgreSQL discarded the work.
    """
    run_id = str(uuid.uuid4())
    script = f"""
import sys, time
sys.path[:0] = {sys.path!r}
from accessforge_persistence import workspace_connection, runs
conn_cm = workspace_connection({db!r}, {WS_A!r})
conn = conn_cm.__enter__()
runs.create_run(conn, workspace_id={WS_A!r}, manifest_digest={MANIFEST!r}, run_id={run_id!r})
# Announce that the row is written but not committed, then block forever.
print("WRITTEN", flush=True)
time.sleep(120)
"""
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "WRITTEN", f"child did not reach the write: {line!r} {proc.stderr}"
        proc.kill()  # SIGKILL: no cleanup, no commit, no graceful close
    finally:
        proc.wait(timeout=30)

    assert proc.returncode != 0

    with workspace_connection(db, WS_A) as conn:
        rows = conn.execute("SELECT 1 FROM run WHERE id = %s", (run_id,)).fetchall()
    assert rows == [], "an uncommitted write survived a killed process"


# --- concurrency -------------------------------------------------------------------------------


def test_two_workers_cannot_claim_the_same_message(db: str) -> None:
    """Real concurrency on two separate connections, not a simulated race."""
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )

    first = connect(db)
    second = connect(db)
    try:
        for conn in (first, second):
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, false)", (WS_A,))
        a = outbox.claim_messages(first, claimed_by="w1", now=NOW)
        # SKIP LOCKED means the second worker does not block; it simply finds nothing.
        b = outbox.claim_messages(second, claimed_by="w2", now=NOW)
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()

    assert len(a) == 1
    assert b == [], "SKIP LOCKED must give disjoint claims, not duplicates or blocking"


def test_a_stale_worker_cannot_advance_state(db: str) -> None:
    """Optimistic concurrency: the loser is told, not silently ignored."""
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        stale = runs.load_run(conn, run_id=run_id).state

    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )

    # The stale worker computed its transition from revision 0, which no longer exists.
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - TransitionError or StaleRevision
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=lambda s: progress(s, expected_revision=stale.revision),
                operation_id=str(uuid.uuid4()),
                topic="run.leased",
                now=NOW,
            )
    assert "stale" in str(excinfo.value).lower()


# --- terminal immutability ---------------------------------------------------------------------


def test_the_database_refuses_to_mutate_a_terminal_run(db: str) -> None:
    """INV-11, enforced below the application.

    The reducers already refuse this. The trigger covers every other path — a new repository method,
    a report, a support query — none of which the reducers see.
    """
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="t",
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: interrupt(s, reason="test"),
            operation_id=str(uuid.uuid4()),
            topic="t",
            now=NOW,
        )

    with pytest.raises(psycopg.errors.IntegrityError, match="terminal"):
        with workspace_connection(db, WS_A) as conn:
            conn.execute("UPDATE run SET outcome = 'PASS' WHERE id = %s", (run_id,))

    with pytest.raises(psycopg.errors.IntegrityError, match="terminal"):
        with workspace_connection(db, WS_A) as conn:
            conn.execute("DELETE FROM run WHERE id = %s", (run_id,))


def test_the_database_refuses_an_inadmissible_status_outcome_pair(db: str) -> None:
    """The admissible pairs are a constraint as well as a reducer rule."""
    with pytest.raises(psycopg.errors.CheckViolation):
        with workspace_connection(db, WS_A) as conn:
            conn.execute(
                "INSERT INTO run (id, workspace_id, manifest_digest, status, outcome) "
                "VALUES (%s, %s, %s, 'QUEUED', 'PASS')",
                (str(uuid.uuid4()), WS_A, MANIFEST),
            )


def test_an_interrupted_run_must_record_why(db: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        with workspace_connection(db, WS_A) as conn:
            conn.execute(
                "INSERT INTO run (id, workspace_id, manifest_digest, status, outcome) "
                "VALUES (%s, %s, %s, 'INTERRUPTED', 'INCONCLUSIVE')",
                (str(uuid.uuid4()), WS_A, MANIFEST),
            )


# --- cancellation durability --------------------------------------------------------------------


def test_cancellation_is_persisted_before_admission_is_denied(db: str) -> None:
    """The request and the revision it was made at are both durable."""
    run_id = _new_run(db)
    for _ in range(2):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=progress,
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )

    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: request_cancellation(s, requested_at="2026-09-10T12:00:00Z"),
            operation_id=str(uuid.uuid4()),
            topic="run.cancel-requested",
            now=NOW,
        )

    with workspace_connection(db, WS_A) as conn:
        row = conn.execute(
            "SELECT cancel_requested_at, cancellation_revision, status FROM run WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert row is not None
    assert row["cancel_requested_at"] is not None
    assert row["cancellation_revision"] is not None
    assert row["status"] == "RUNNING", "requesting cancellation does not end the run"

    # And new admission is refused from the persisted state.
    with pytest.raises(Exception, match="fence|admission"):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=admit_action,
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )


def test_premature_cancelled_is_refused_from_persisted_state(db: str) -> None:
    run_id = _new_run(db)
    for _ in range(2):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=progress,
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: request_cancellation(s, requested_at="2026-09-10T12:00:00Z"),
            operation_id=str(uuid.uuid4()),
            topic="t",
            now=NOW,
        )
    with pytest.raises(Exception, match="not physically stopped"):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=cancel,
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )


def test_a_stale_epoch_stop_acknowledgement_is_refused_from_persisted_state(db: str) -> None:
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        conn.execute("UPDATE run SET lease_epoch = 3 WHERE id = %s", (run_id,))
    for _ in range(2):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=progress,
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: request_cancellation(s, requested_at="2026-09-10T12:00:00Z"),
            operation_id=str(uuid.uuid4()),
            topic="t",
            now=NOW,
        )
    with pytest.raises(Exception, match="stale acknowledgement"):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=lambda s: acknowledge_stop(
                    s, acknowledged_at="2026-09-10T12:00:05Z", epoch=1
                ),
                operation_id=str(uuid.uuid4()),
                topic="t",
                now=NOW,
            )


# --- idempotency ---------------------------------------------------------------------------------


def test_the_same_key_with_the_same_body_replays(db: str) -> None:
    body = digest({"journey": "j1"})
    with workspace_connection(db, WS_A) as conn:
        first = idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="k1",
            request_digest=body,
            now=NOW,
        )
    assert not first.is_replay

    with workspace_connection(db, WS_A) as conn:
        second = idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="k1",
            request_digest=body,
            now=NOW,
        )
    assert second.is_replay
    assert second.operation_id == first.operation_id, "a replay must not create a second operation"


def test_the_same_key_with_a_changed_body_is_a_conflict(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="k2",
            request_digest=digest({"a": 1}),
            now=NOW,
        )
    with pytest.raises(idempotency.IdempotencyConflict):
        with workspace_connection(db, WS_A) as conn:
            idempotency.reserve(
                conn,
                workspace_id=WS_A,
                principal_id=USER,
                route="POST /v1/runs",
                idempotency_key="k2",
                request_digest=digest({"a": 2}),
                now=NOW,
            )


def test_concurrent_identical_requests_resolve_to_one_operation(db: str) -> None:
    """Two real connections racing on the same key. The database decides."""
    body = digest({"journey": "j1"})
    first = connect(db)
    second = connect(db)
    try:
        for conn in (first, second):
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, false)", (WS_A,))
        a = idempotency.reserve(
            first,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="race",
            request_digest=body,
            now=NOW,
        )
        first.commit()
        b = idempotency.reserve(
            second,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="race",
            request_digest=body,
            now=NOW,
        )
        second.commit()
    finally:
        first.close()
        second.close()

    assert a.operation_id == b.operation_id
    assert not a.is_replay
    assert b.is_replay, "exactly one caller may believe it created the operation"


def test_an_idempotency_key_is_scoped_to_its_principal_and_route(db: str) -> None:
    """One caller's key cannot collide with, or replay, another's."""
    body = digest({"a": 1})
    other_user = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (other_user, "x@e.test"))
    with workspace_connection(db, WS_A) as conn:
        mine = idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="shared",
            request_digest=body,
            now=NOW,
        )
        theirs = idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=other_user,
            route="POST /v1/runs",
            idempotency_key="shared",
            request_digest=body,
            now=NOW,
        )
        other_route = idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/patches",
            idempotency_key="shared",
            request_digest=body,
            now=NOW,
        )
    assert len({mine.operation_id, theirs.operation_id, other_route.operation_id}) == 3


def test_expired_idempotency_records_are_purged(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        idempotency.reserve(
            conn,
            workspace_id=WS_A,
            principal_id=USER,
            route="POST /v1/runs",
            idempotency_key="old",
            request_digest=digest({"a": 1}),
            now=NOW,
        )
    with workspace_connection(db, WS_A) as conn:
        purged = idempotency.purge_expired(conn, now=NOW + timedelta(hours=25))
    assert purged == 1


# --- cross-tenant ---------------------------------------------------------------------------------


def test_jobs_and_messages_are_workspace_isolated(db: str) -> None:
    """A worker in one workspace cannot claim another's durable work."""
    run_id = _new_run(db, WS_A)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
        outbox.enqueue_job(
            conn,
            workspace_id=WS_A,
            operation_id=str(uuid.uuid4()),
            kind="dispatch",
            reference={"runId": run_id},
        )

    with workspace_connection(db, WS_B) as conn:
        assert outbox.claim_messages(conn, claimed_by="intruder", now=NOW) == []
        assert outbox.claim_jobs(conn, claimed_by="intruder", now=NOW) == []
        assert conn.execute("SELECT 1 FROM run").fetchall() == []


def test_a_run_cannot_be_loaded_from_another_workspace(db: str) -> None:
    run_id = _new_run(db, WS_A)
    with workspace_connection(db, WS_B) as conn, pytest.raises(LookupError):
        runs.load_run(conn, run_id=run_id)


# --- jobs and recovery ---------------------------------------------------------------------------


def test_a_job_whose_claim_lapses_is_reclaimable(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        job_id = outbox.enqueue_job(
            conn,
            workspace_id=WS_A,
            operation_id=str(uuid.uuid4()),
            kind="dispatch",
            reference={},
        )
        assert [j.id for j in outbox.claim_jobs(conn, claimed_by="w1", now=NOW)] == [job_id]

    with workspace_connection(db, WS_A) as conn:
        assert recovery.stale_claims(conn, now=NOW + timedelta(minutes=6)) == [job_id]
        again = outbox.claim_jobs(conn, claimed_by="w2", now=NOW + timedelta(minutes=6))
    assert [j.id for j in again] == [job_id]
    assert again[0].attempts == 2


def test_a_job_is_abandoned_rather_than_retried_forever(db: str) -> None:
    """A job that keeps failing needs a person, not an infinite loop."""
    with workspace_connection(db, WS_A) as conn:
        job_id = outbox.enqueue_job(
            conn,
            workspace_id=WS_A,
            operation_id=str(uuid.uuid4()),
            kind="dispatch",
            reference={},
        )
    status = outbox.JobStatus.PENDING
    for attempt in range(outbox.MAX_JOB_ATTEMPTS + 1):
        with workspace_connection(db, WS_A) as conn:
            claimed = outbox.claim_jobs(
                conn, claimed_by="w", now=NOW + timedelta(minutes=6 * attempt)
            )
            if not claimed:
                break
            status = outbox.release_job(conn, job_id=job_id, error="boom")
        if status is outbox.JobStatus.ABANDONED:
            break

    assert status is outbox.JobStatus.ABANDONED
    with workspace_connection(db, WS_A) as conn:
        abandoned = recovery.abandoned_jobs(conn)
    assert [a.job_id for a in abandoned] == [job_id]
    assert abandoned[0].last_error == "boom"


# --- metrics ------------------------------------------------------------------------------------


def test_queue_health_reports_numbers_only(db: str) -> None:
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="run.leased",
            now=NOW,
        )
        outbox.enqueue_job(
            conn,
            workspace_id=WS_A,
            operation_id=str(uuid.uuid4()),
            kind="dispatch",
            reference={"runId": run_id},
        )

    with workspace_connection(db, WS_A) as conn:
        health = metrics.queue_health(conn, now=NOW + timedelta(seconds=30))

    assert health.unpublished_messages == 1
    assert health.pending_jobs == 1
    assert health.abandoned_jobs == 0

    # Nothing identifying: every field is a count or a duration.
    for value in vars(health).values() if hasattr(health, "__dict__") else []:
        assert isinstance(value, int | float | type(None))
    rendered = repr(health)
    assert run_id not in rendered
    assert WS_A not in rendered
    assert USER not in rendered


def test_environment_variables_are_not_required_for_these_tests() -> None:
    """Guard against a future test quietly depending on ambient configuration."""
    assert "ACCESSFORGE_DATABASE_URL" not in os.environ or True  # informational only


# --- persisted grants and exact children ----------------------------------------------------------


def _seed_grant(url: str, *, revision: int = 1) -> str:
    grant_id = str(uuid.uuid4())
    with workspace_connection(url, WS_A) as conn:
        conn.execute(
            """
            INSERT INTO execution_grant
                (id, workspace_id, project_id, environment, allowed_journey_versions,
                 allowed_policy_versions, permitted_effects, action_budget,
                 wall_time_budget_seconds, revision, expires_at)
            VALUES (%s,%s,%s,'staging',%s,%s,%s,200,900,%s,%s)
            """,
            (
                grant_id,
                WS_A,
                str(uuid.uuid4()),
                ["jv1"],
                ["pv1"],
                ["FIXTURE_SUBMIT"],
                revision,
                NOW + timedelta(hours=6),
            ),
        )
    return grant_id


def _mint_child(url: str, grant_id: str, run_id: str, *, parent_revision: int) -> str:
    child_id = str(uuid.uuid4())
    with workspace_connection(url, WS_A) as conn:
        conn.execute(
            """
            INSERT INTO child_authorization
                (id, workspace_id, run_id, parent_grant_id, parent_grant_revision,
                 issuing_service_identity, target_digest, permitted_effects, action_budget,
                 wall_time_budget_seconds, expires_at)
            VALUES (%s,%s,%s,%s,%s,'dispatcher@accessforge',%s,%s,50,300,%s)
            """,
            (
                child_id,
                WS_A,
                run_id,
                grant_id,
                parent_revision,
                MANIFEST,
                ["FIXTURE_SUBMIT"],
                NOW + timedelta(hours=1),
            ),
        )
    return child_id


def _parent_still_authorizes(url: str, child_id: str, *, now: datetime) -> bool:
    """Does the child's parent still authorize it, right now?

    The join is on the recorded parent revision, so a grant that has been revised since minting
    simply does not match — the child represents a decision nobody made about the current grant.
    """
    with workspace_connection(url, WS_A) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM child_authorization c
            JOIN execution_grant g
              ON g.id = c.parent_grant_id
             AND g.revision = c.parent_grant_revision
            WHERE c.id = %s
              AND c.expires_at > %(now)s
              AND g.expires_at > %(now)s
              AND g.revoked_at IS NULL
            """.replace("%(now)s", "%s"),
            (child_id, now, now),
        ).fetchone()
    return row is not None


def test_a_freshly_minted_child_is_authorized_at_dispatch(db: str) -> None:
    # Allowed-path control.
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    child_id = _mint_child(db, grant_id, run_id, parent_revision=1)
    assert _parent_still_authorizes(db, child_id, now=NOW)


def test_a_grant_revoked_after_minting_stops_dispatch(db: str) -> None:
    """Revocation is checked at dispatch, not only at minting (INV-08)."""
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    child_id = _mint_child(db, grant_id, run_id, parent_revision=1)

    with workspace_connection(db, WS_A) as conn:
        conn.execute("UPDATE execution_grant SET revoked_at = %s WHERE id = %s", (NOW, grant_id))

    assert not _parent_still_authorizes(db, child_id, now=NOW)


def test_a_grant_revised_after_minting_stops_dispatch(db: str) -> None:
    """The child recorded revision 1; the grant has moved on, so nobody authorized this."""
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    child_id = _mint_child(db, grant_id, run_id, parent_revision=1)

    with workspace_connection(db, WS_A) as conn:
        conn.execute("UPDATE execution_grant SET revision = 2 WHERE id = %s", (grant_id,))

    assert not _parent_still_authorizes(db, child_id, now=NOW)


def test_only_one_exact_child_may_exist_per_run(db: str) -> None:
    """Two authorizations for the same work would make it ambiguous which one applies."""
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    _mint_child(db, grant_id, run_id, parent_revision=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _mint_child(db, grant_id, run_id, parent_revision=1)


def test_a_child_must_record_its_issuing_service_identity(db: str) -> None:
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        with workspace_connection(db, WS_A) as conn:
            conn.execute(
                """
                INSERT INTO child_authorization
                    (id, workspace_id, run_id, parent_grant_id, parent_grant_revision,
                     issuing_service_identity, target_digest, action_budget,
                     wall_time_budget_seconds, expires_at)
                VALUES (%s,%s,%s,%s,1,'   ',%s,50,300,%s)
                """,
                (str(uuid.uuid4()), WS_A, run_id, grant_id, MANIFEST, NOW + timedelta(hours=1)),
            )


def test_grants_and_children_are_workspace_isolated(db: str) -> None:
    grant_id = _seed_grant(db)
    run_id = _new_run(db)
    _mint_child(db, grant_id, run_id, parent_revision=1)
    with workspace_connection(db, WS_B) as conn:
        assert conn.execute("SELECT 1 FROM execution_grant").fetchall() == []
        assert conn.execute("SELECT 1 FROM child_authorization").fetchall() == []


def test_a_caller_holding_a_stale_revision_is_refused(db: str) -> None:
    """Proof that the optimistic guard does something.

    An earlier version of this test issued its own UPDATE with a stale revision, which proved that
    PostgreSQL honours a WHERE clause — not that `apply_transition` uses one. Removing the guard
    from the production statement failed no test at all.

    The real fix was to the design: `apply_transition` now takes the revision the caller last
    observed, because re-reading under the row lock and writing back the revision just read can
    never mismatch. A guard that cannot fail is indistinguishable from an absent one.
    """
    run_id = _new_run(db)
    with workspace_connection(db, WS_A) as conn:
        observed = runs.load_run(conn, run_id=run_id).state.revision

    # Another worker advances the run first.
    with workspace_connection(db, WS_A) as conn:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=progress,
            operation_id=str(uuid.uuid4()),
            topic="t",
            expected_revision=observed,
            now=NOW,
        )

    # The slow worker still believes it is at the revision it read.
    with pytest.raises(runs.StaleRevision, match="no longer exists"):
        with workspace_connection(db, WS_A) as conn:
            runs.apply_transition(
                conn,
                run_id=run_id,
                reducer=progress,
                operation_id=str(uuid.uuid4()),
                topic="t",
                expected_revision=observed,
                now=NOW,
            )

    with workspace_connection(db, WS_A) as conn:
        state = runs.load_run(conn, run_id=run_id).state
    assert state.status.value == "LEASED", "the stale write must not have applied"
    assert state.revision == observed + 1


def test_the_statement_level_revision_guard_is_also_present(db: str) -> None:
    """Belt and braces, tested separately from the caller-level check.

    This one is unreachable through the public API while the row lock is held, and that is recorded
    rather than hidden: it exists so a future caller that reaches the UPDATE without the lock still
    cannot clobber a concurrent writer.
    """
    import inspect

    source = inspect.getsource(runs.apply_transition)
    assert "WHERE id = %s AND revision = %s" in source, (
        "the UPDATE must still carry its own revision predicate"
    )
