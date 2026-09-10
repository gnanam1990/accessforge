"""Forward migration from the previous schema, and what an abrupt kill leaves behind.

Two questions module 27 has to answer with a real database rather than an argument.

**Can this release migrate the schema the previous release wrote?** Answered by building a database
at the previous migration, applying the tree, and checking that data written under the old rules
survives and that the new rules are in force. A migration test that started from an empty database
would prove that the SQL parses, which is not the question.

**What does an abrupt kill leave?** A worker killed mid-transaction leaves a claimed job and an
unpublished outbox row, because that is what a rollback leaves. Both are recoverable *by state*
rather than by remembering what the dead process was doing, and both survive a backup taken while
they are in that condition. The test kills the backend for real -- `pg_terminate_backend` -- rather
than closing a connection politely, because a polite close rolls back cleanly and an SPI-level kill
is what a machine losing power actually does.

Requirements: FR-015, FR-020, FR-022. Invariants: INV-09, INV-10, INV-11.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from accessforge_persistence import (
    MIGRATIONS_DIR,
    connect,
    expected_migrations,
    migrate,
    outbox,
    restore,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x2B0))

#: The migration this release adds on top of the previous one. Named rather than computed, so that
#: adding a migration without extending this test is a failure rather than a silent widening.
NEWEST = "0016_schedule_reapproval.sql"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture()
def disposable(backup_database_url: str) -> Iterator[str]:
    name = f"accessforge_fwd_{uuid.uuid4().hex[:8]}"
    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - generated name, not user input
    try:
        yield _with_database(backup_database_url, name)
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608


def _apply_through(database_url: str, last: str) -> None:
    """Build a database at exactly one migration, the way the previous release left it.

    The migrator itself always applies everything, so this deliberately bypasses it and writes the
    ledger by hand -- which is also the only way to produce the "previous release" state without
    checking out the previous release.
    """
    with connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
            if path.name == last:
                break
        conn.commit()


def _previous() -> str:
    names = expected_migrations()
    assert names[-1] == NEWEST, (
        f"the newest migration is {names[-1]}, not {NEWEST}. This test names the boundary "
        "explicitly so that adding a migration without extending the forward-migration drill "
        "fails here rather than shipping untested."
    )
    return names[-2]


def test_a_database_at_the_previous_schema_migrates_forward(disposable: str) -> None:
    previous = _previous()
    _apply_through(disposable, previous)

    with connect(disposable) as conn:
        before = restore.schema_state(conn)
    assert before.latest == previous
    assert NEWEST not in before.applied

    applied = migrate(disposable)
    assert applied == [NEWEST]

    with connect(disposable) as conn:
        after = restore.schema_state(conn)
    assert after.latest == NEWEST


def test_data_written_under_the_previous_rules_survives_the_migration(disposable: str) -> None:
    """The half of a migration test that an empty-database run cannot reach.

    A released lease with an old-vocabulary reason is written before the migration and read after
    it. Widening a CHECK constraint keeps old rows valid by construction, and "by construction" is
    exactly the kind of claim this project does not accept without a row to point at.
    """
    _apply_through(disposable, _previous())
    lease = _seed_released_lease(disposable, reason="OPERATOR_RESET")

    migrate(disposable)

    with connect(disposable) as conn:
        row = conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id = %s", (lease,)
        ).fetchone()
    assert row is not None and row["release_reason"] == "OPERATOR_RESET"


def test_the_newest_migrations_effect_is_absent_before_and_present_after(
    disposable: str,
) -> None:
    """The newest migration's actual effect, in both directions.

    Asserting only that the new thing works afterwards would pass against a database where the
    constraint had been dropped entirely, or the column had always been there -- and "the guard is
    gone" is a much worse outcome than "the feature is missing".
    """
    _apply_through(disposable, _previous())
    with connect(disposable) as conn:
        before = conn.execute(
            "SELECT count(*) AS n FROM information_schema.columns "
            " WHERE table_name = 'schedule' AND column_name IN ('reapproved_at','reapproved_by')"
        ).fetchone()
    assert before is not None and int(before["n"]) == 0

    migrate(disposable)

    with connect(disposable) as conn:
        after = conn.execute(
            "SELECT count(*) AS n FROM information_schema.columns "
            " WHERE table_name = 'schedule' AND column_name IN ('reapproved_at','reapproved_by')"
        ).fetchone()
        # The pairing constraint, not just the columns. Half a re-approval record -- a timestamp
        # with nobody attached -- is the thing the constraint exists to make impossible, and
        # asserting only that the columns arrived would pass without it.
        constraint = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'reapproval_is_attributable'"
        ).fetchone()
    assert after is not None and int(after["n"]) == 2
    assert constraint is not None


def test_the_grant_revalidation_column_from_an_earlier_migration_is_still_correct(
    disposable: str,
) -> None:
    """Migration 0015's effect, kept as its own case now that it is no longer the newest.

    NOT NULL DEFAULT false: an existing grant keeps working, because "a restore brought you back and
    nobody has confirmed you" is not true of a grant that has been live all along.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        column = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            " WHERE table_name = 'execution_grant' AND column_name = 'revalidation_required'"
        ).fetchone()
    assert column is not None
    assert column["is_nullable"] == "NO"
    assert "false" in str(column["column_default"])


def test_an_older_release_reason_survives_and_a_nonsense_one_is_still_refused(
    disposable: str,
) -> None:
    """Migration 0014's widening, kept as its own case now that it is no longer the newest.

    A migration test that only ever covered the tip would stop exercising every earlier change the
    moment another one landed, which is precisely when a regression in one of them would ship.
    """
    migrate(disposable)
    assert _seed_released_lease(disposable, reason="RESTORED_DATABASE")
    assert _seed_released_lease(disposable, reason="OPERATOR_RESET")

    # Widened to a named set, not to anything.
    with pytest.raises(psycopg.errors.CheckViolation):
        _seed_released_lease(disposable, reason="TIDIED_UP")


def test_a_schema_ahead_of_this_build_is_refused_rather_than_downgraded(disposable: str) -> None:
    migrate(disposable)
    with connect(disposable) as conn:
        conn.execute("INSERT INTO schema_migration (name) VALUES ('0099_from_the_future.sql')")
        conn.commit()
        ok, detail = restore.restore_is_forward_compatible(conn, expected=expected_migrations())
    assert ok is False
    assert "newer release" in detail
    assert "code rollback does not reverse a data migration" in detail


def _seed_released_lease(database_url: str, *, reason: str) -> str:
    """A workspace, a runner, a run, an attempt and a released lease. Enough to exercise the check.

    Written through an elevated connection because the disposable database has no application role
    and this is testing schema rather than isolation.
    """
    from accessforge_persistence import runs

    lease_id = str(uuid.uuid4())
    with connect(database_url) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        existing = conn.execute("SELECT 1 FROM workspace WHERE id = %s", (WS,)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Forward')", (WS,))
        runner_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO runner (id, workspace_id, name, status, session_key, platform, device_id,
                                interactive_session_id, console, profile_digest, profile,
                                lease_epoch)
            VALUES (%s, %s, %s, 'OFFLINE', %s, 'darwin', 'device', 'session', true, %s,
                    '{"readerName": "VoiceOver"}', 1)
            """,
            (runner_id, WS, f"desk-{runner_id[:8]}", uuid.uuid4().hex * 2, "e" * 64),
        )
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest="f" * 64)
        attempt = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch, deadline_at,
                 released_at, release_reason)
            VALUES (%s, %s, %s, %s, %s, %s, 1, now() + interval '1 hour', now(), %s)
            """,
            (lease_id, WS, runner_id, uuid.uuid4().hex * 2, run_id, attempt, reason),
        )
        conn.commit()
    return lease_id


def _seed_queue(disposable: str) -> None:
    migrate(disposable)
    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Interrupt')", (WS,))
        outbox.enqueue_job(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            kind="finalize",
            reference={"runId": "x"},
        )
        outbox.enqueue_message(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            topic="run.finished",
            reference={"runId": "x"},
        )
        conn.commit()


def _kill(conn: psycopg.Connection[dict[str, object]], killer_url: str) -> None:
    """Terminate a backend for real, rather than closing its connection politely.

    A polite close rolls back through the driver, which is the case that already works.
    `pg_terminate_backend` is what a machine losing power does to a session.
    """
    backend = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()
    assert backend is not None
    with connect(killer_url) as killer:
        killer.autocommit = True
        killer.execute("SELECT pg_terminate_backend(%s)", (backend["pid"],))


def test_a_worker_killed_before_committing_its_claim_loses_nothing(disposable: str) -> None:
    """The claim is not durable until it commits, so an abrupt kill returns the job to PENDING.

    This is the outcome, not the assumption: the first version of this test asserted the job stayed
    CLAIMED and failed, because `claim_jobs` leaves the transaction open for the caller to commit
    alongside whatever else the job does. That is the right design -- a claim that committed
    separately from the work would be a claim that can outlive a rollback of the work -- and it
    means a kill in this window costs nothing at all.
    """
    _seed_queue(disposable)

    victim = psycopg.connect(disposable, row_factory=psycopg.rows.dict_row, autocommit=False)
    victim.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
    assert len(outbox.claim_jobs(victim, claimed_by="doomed-worker", limit=5)) == 1
    _kill(victim, disposable)

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by, attempts FROM job").fetchone()
        assert job is not None
        assert job["status"] == "PENDING"
        assert job["claimed_by"] is None
        # Even the attempt counter rolled back. Nothing observed the dead worker at all.
        assert int(job["attempts"]) == 0
        assert outbox.unpublished_count(conn) == 1


def test_a_worker_killed_after_committing_its_claim_leaves_a_stale_claim(disposable: str) -> None:
    """The window that does cost something, and what makes it recoverable.

    A worker that commits its claim and then dies leaves a row saying a process that no longer
    exists owns this work. It is recoverable *by state* -- the claim carries an expiry, and nothing
    has to remember what the dead process was doing -- which is the property that lets a restore
    reason about it at all.
    """
    _seed_queue(disposable)

    victim = psycopg.connect(disposable, row_factory=psycopg.rows.dict_row, autocommit=False)
    victim.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
    assert len(outbox.claim_jobs(victim, claimed_by="doomed-worker", limit=5)) == 1
    victim.commit()
    _kill(victim, disposable)

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by, claim_expires_at FROM job").fetchone()
        assert job is not None
        assert job["status"] == "CLAIMED"
        assert job["claimed_by"] == "doomed-worker"
        assert job["claim_expires_at"] is not None
        assert outbox.unpublished_count(conn) == 1


def test_reconciliation_releases_a_claim_left_by_a_killed_worker(disposable: str) -> None:
    """The recovery, end to end: the state a kill leaves is exactly what reconciliation fixes.

    Released rather than deleted. A job is a database operation and re-reading state makes a repeat
    harmless; the claim is what is stale, not the work. Deleting it would lose the work with no
    record that anything was dropped.
    """
    test_a_worker_killed_after_committing_its_claim_leaves_a_stale_claim(disposable)

    with connect(disposable) as conn:
        report = restore.reconcile(conn, operator="interruption-drill")
        conn.commit()
    assert report.jobs_released == 1
    assert report.outbox_messages_suppressed == 1

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by FROM job").fetchone()
        assert job is not None
        assert job["status"] == "PENDING"
        assert job["claimed_by"] is None
        assert outbox.unpublished_count(conn) == 0
