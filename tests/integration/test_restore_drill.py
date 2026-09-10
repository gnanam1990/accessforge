"""A real backup and a real restore into a disposable database, then reconciliation.

Not a simulation. Each test dumps a populated database with `pg_dump`, creates a throwaway target,
restores into it with `psql`, and reconciles — then asserts what survived and what did not.

The distinction the whole module turns on: **a restore brings back authority along with evidence,
and only one of the two is still true.** Leases, sessions, enrollment tokens, claimed jobs and
undelivered outbox rows were all valid at the moment of the snapshot and are all stale now.
Evidence, verdicts, audit rows and terminal runs were true then and are true now.

Reconciliation is therefore destructive in exactly one direction. These tests check both halves: it
invalidates every credential and lease, and it changes no run's status, no artifact and no audit
row.

Requirements: FR-015, FR-020, FR-022. Invariants: INV-03, INV-06, INV-08, INV-09, INV-10, INV-11.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from accessforge_domain.authority import AuthorityError
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    MIGRATIONS_DIR,
    connect,
    migrate,
    restore,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x290))
USER = str(uuid.UUID(int=0x291))
MANIFEST = digest({"manifest": "restore"})


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        capture_output=True,
        text=True,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )
    if result.returncode != 0:
        raise AssertionError(f"{command[0]} failed: {result.stderr[-2000:]}")
    return result


@pytest.fixture()
def populated(test_database_url: str) -> Iterator[str]:
    """A database holding live authority and real evidence, ready to be backed up."""
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
        conn.execute("DELETE FROM global_audit_event")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Restore')", (WS,))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'operator@example.test')", (USER,)
        )
        # A live session, valid at snapshot time.
        conn.execute(
            """
            INSERT INTO user_session (id, user_id, token_hash, csrf_token_hash, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), USER, "a" * 64, "b" * 64, datetime.now(UTC) + timedelta(hours=6)),
        )

    later = datetime.now(UTC) + timedelta(hours=1)
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'OWNER')",
            (WS, USER),
        )
        # An unredeemed enrollment token: desktop input authority sitting in a backup.
        conn.execute(
            """
            INSERT INTO runner_enrollment_token
                (id, workspace_id, token_digest, expires_at, created_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), WS, "c" * 64, later, USER),
        )
        runner_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO runner (id, workspace_id, name, status, session_key, platform, device_id,
                                interactive_session_id, console, profile_digest, profile,
                                lease_epoch)
            VALUES (%s, %s, 'desk-1', 'BUSY', %s, 'darwin', 'device-1', 'session-1', true, %s,
                    '{"readerName": "VoiceOver"}', 1)
            """,
            (runner_id, WS, "d" * 64, "e" * 64),
        )

        # A run in flight with an unresolved action, and a terminal run beside it.
        in_flight = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt = runs.start_attempt(conn, run_id=in_flight, workspace_id=WS, lease_epoch=1)
        conn.execute(
            "UPDATE run SET status = 'RUNNING', unresolved_action = true WHERE id = %s",
            (in_flight,),
        )
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch, deadline_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
            """,
            (str(uuid.uuid4()), WS, runner_id, "d" * 64, in_flight, attempt, later),
        )
        sequencer.admit_record(
            conn,
            workspace_id=WS,
            run_id=in_flight,
            attempt_id=attempt,
            lease_epoch=1,
            producer_id="observer-1",
            source_record_id="evidence-1",
            producer_sequence=1,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"phrase": "evidence that must survive"},
            source_time=datetime(2026, 9, 10, 12, tzinfo=UTC),
        )

        terminal = runs.create_run(conn, workspace_id=WS, manifest_digest=digest({"m": "done"}))
        conn.execute(
            "UPDATE run SET status = 'COMPLETED', outcome = 'FAIL' WHERE id = %s", (terminal,)
        )

        # Durable work mid-flight: a claimed job and an unpublished outbox row.
        conn.execute(
            """
            INSERT INTO job (id, workspace_id, operation_id, kind, reference, status,
                             claim_expires_at, claimed_by)
            VALUES (%s, %s, %s, 'finalize', '{}', 'CLAIMED', %s, 'worker-1')
            """,
            (str(uuid.uuid4()), WS, str(uuid.uuid4()), later),
        )
        conn.execute(
            """
            INSERT INTO outbox_message (workspace_id, operation_id, topic, reference)
            VALUES (%s, %s, 'run.finished', '{"runId": "x"}')
            """,
            (WS, str(uuid.uuid4())),
        )
    yield test_database_url


@pytest.fixture()
def restored_admin_url(request: pytest.FixtureRequest) -> str:
    """The elevated connection to the restored database, for reconciliation."""
    return str(request.getfixturevalue("restored_pair")[1])


@pytest.fixture()
def restored(restored_pair: tuple[str, str]) -> str:
    """The restored database as the *application* role sees it."""
    return restored_pair[0]


@pytest.fixture()
def restored_pair(populated: str, backup_database_url: str) -> Iterator[tuple[str, str]]:
    """`pg_dump` the populated database, create a disposable target, `psql` it back.

    Dumped through `backup_database_url` rather than the application connection, because the
    application role cannot read its own rows — see `test_the_application_role_cannot_take_a_backup`
    and the fixture's docstring.
    """
    target = f"accessforge_restore_{uuid.uuid4().hex[:8]}"
    # A faithful dump: ownership and grants included. `--no-owner --no-privileges` is the reflex
    # for moving a database between environments, and here it produces a restore the application
    # role cannot read at all — the tables come back owned by whoever ran the restore, with no
    # grants, and the security model this product rests on is *expressed* as ownership plus FORCE
    # row-level security. See `test_a_dump_that_discards_ownership_produces_an_unusable_restore`.
    dump = _run(["pg_dump", backup_database_url]).stdout

    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{target}"')  # noqa: S608 - generated name, not user input

    # Restored through the backup role too: the dump recreates the tables, and the role that creates
    # them becomes their owner. Restoring as the application role would make it the owner of a fresh
    # set of tables whose FORCE policies it is then subject to, which is correct — but it cannot
    # execute the dump's own `COPY` statements for the same reason it cannot produce them.
    target_url = _with_database(backup_database_url, target)
    _run(["psql", "--quiet", "-v", "ON_ERROR_STOP=1", target_url], input=dump)
    try:
        # Both URLs: the application role's, so every assertion runs under row-level security
        # exactly as the product does, and the elevated one, because reconciliation cannot run
        # under RLS — it would see none of the rows it must invalidate.
        yield _with_database(populated, target), target_url
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')  # noqa: S608


def test_a_dump_that_discards_ownership_produces_an_unusable_restore(
    populated: str, backup_database_url: str
) -> None:
    """`pg_dump --no-owner --no-privileges` is the reflex, and it is wrong here.

    This product's isolation is expressed as ownership plus `FORCE ROW LEVEL SECURITY`. A dump that
    discards ownership restores tables owned by whoever ran the restore, with no grants — so the
    application role cannot read them at all. The failure arrives at the first query after a restore
    somebody believed had worked.
    """
    target = f"accessforge_noowner_{uuid.uuid4().hex[:8]}"
    dump = _run(["pg_dump", "--no-owner", "--no-privileges", backup_database_url]).stdout
    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{target}"')  # noqa: S608 - generated name
    try:
        _run(
            [
                "psql",
                "--quiet",
                "-v",
                "ON_ERROR_STOP=1",
                _with_database(backup_database_url, target),
            ],
            input=dump,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with workspace_connection(_with_database(populated, target), WS) as conn:
                conn.execute("SELECT count(*) FROM canonical_event").fetchone()
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')  # noqa: S608


def test_the_application_role_cannot_take_a_backup(populated: str) -> None:
    """An operational fact, captured as a test rather than a line in a runbook.

    Every tenant-owned table has `FORCE ROW LEVEL SECURITY`, which applies to the table owner. So
    `pg_dump` run as the application role does not produce a smaller backup — it fails, because it
    cannot read the rows it owns. A backup script running as the application role produces no backup
    and finds out at restore time.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        # `pg_dump` from PATH, as an operator's backup script would run it. Pinning an absolute
        # path here would test a different thing from the one under test.
        ["pg_dump", "--no-owner", "--no-privileges", populated],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "row-level security policy" in result.stderr


# --------------------------------------------------------------------------------------------------
# The restore itself
# --------------------------------------------------------------------------------------------------


def test_a_restore_brings_back_authority_that_is_no_longer_true(restored: str) -> None:
    """The problem reconciliation exists to solve, asserted before it runs.

    Straight after a restore the database says a session is live, a desktop is leased and an
    enrollment token is unredeemed. All three were true at the snapshot and none is true now.
    """
    with unscoped_connection(restored) as conn:
        live_sessions = conn.execute(
            "SELECT count(*) AS n FROM user_session WHERE revoked_at IS NULL"
        ).fetchone()
        assert live_sessions and int(live_sessions["n"]) == 1
    with workspace_connection(restored, WS) as conn:
        held = conn.execute(
            "SELECT count(*) AS n FROM desktop_lease WHERE released_at IS NULL"
        ).fetchone()
        assert held and int(held["n"]) == 1


def test_evidence_survives_the_restore_intact(restored: str) -> None:
    with workspace_connection(restored, WS) as conn:
        row = conn.execute(
            "SELECT payload, payload_digest FROM canonical_event WHERE sequence = 1"
        ).fetchone()
    assert row is not None
    assert dict(row["payload"]) == {"phrase": "evidence that must survive"}
    # The digest still matches its payload: a restore that re-encoded JSON would break every chain
    # in the database and every offline verification of every export taken from it.
    assert str(row["payload_digest"]) == digest(dict(row["payload"]))


# --------------------------------------------------------------------------------------------------
# Reconciliation: what it invalidates
# --------------------------------------------------------------------------------------------------


def test_reconciliation_invalidates_every_credential_and_lease(
    restored: str, restored_admin_url: str
) -> None:
    with connect(restored_admin_url) as conn:
        report = restore.reconcile(conn, operator="drill")

    assert report.sessions_revoked == 1
    assert report.enrollment_tokens_expired == 1
    assert report.leases_fenced == 1
    assert report.runners_quarantined == 1
    assert report.jobs_released == 1
    assert report.outbox_messages_suppressed == 1

    with unscoped_connection(restored) as conn:
        live = conn.execute(
            "SELECT count(*) AS n FROM user_session WHERE revoked_at IS NULL"
        ).fetchone()
        assert live and int(live["n"]) == 0
    with workspace_connection(restored, WS) as conn:
        held = conn.execute(
            "SELECT count(*) AS n FROM desktop_lease WHERE released_at IS NULL"
        ).fetchone()
        assert held and int(held["n"]) == 0
        # Released with a reason, not reassigned. The previous supervisor may still be running
        # against a desktop this database can no longer see.
        reason = conn.execute("SELECT release_reason FROM desktop_lease LIMIT 1").fetchone()
        assert reason and str(reason["release_reason"]) == "RESTORED_DATABASE"


def test_a_restored_runner_is_quarantined_rather_than_available(
    restored: str, restored_admin_url: str
) -> None:
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
    with workspace_connection(restored, WS) as conn:
        row = conn.execute("SELECT status, quarantine_reason FROM runner").fetchone()
    assert row is not None
    assert str(row["status"]) == "QUARANTINED"
    # A quarantine is released by a trusted reset and a fresh preflight, which is exactly the proof
    # a restore cannot supply.
    assert "cannot be proven to have stopped" in str(row["quarantine_reason"])


def test_an_ambiguous_attempt_stays_ambiguous_and_is_quarantined(
    restored: str, restored_admin_url: str
) -> None:
    """INV-09 across a restore.

    An action was dispatched and its result never recorded. A restore is not evidence that it did
    not happen, and re-dispatching it is the one thing this system must never do.
    """
    with connect(restored_admin_url) as conn:
        report = restore.reconcile(conn, operator="drill")
    assert report.attempts_quarantined == 1
    with workspace_connection(restored, WS) as conn:
        row = conn.execute(
            "SELECT quarantined, unresolved_action, status FROM run WHERE unresolved_action"
        ).fetchone()
    assert row is not None
    assert bool(row["quarantined"]) is True
    # Still unresolved, and still not terminal: reconciliation records that nobody may act, and
    # decides nothing about what happened.
    assert bool(row["unresolved_action"]) is True
    assert str(row["status"]) == "RUNNING"


def test_undelivered_outbox_messages_are_suppressed_rather_than_redelivered(
    restored: str, restored_admin_url: str
) -> None:
    """They announce state changes that already happened.

    Redelivering a backup's worth of announcements tells every consumer that a day of events is
    happening again right now.
    """
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
    with workspace_connection(restored, WS) as conn:
        pending = conn.execute(
            "SELECT count(*) AS n FROM outbox_message WHERE published_at IS NULL"
        ).fetchone()
    assert pending and int(pending["n"]) == 0


def test_a_claimed_job_is_released_rather_than_deleted(
    restored: str, restored_admin_url: str
) -> None:
    """The claim is stale; the work is not.

    A job is a database operation and re-reading state makes a repeat harmless — which is exactly
    what makes it different from a desktop action.
    """
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
    with workspace_connection(restored, WS) as conn:
        row = conn.execute("SELECT status, claimed_by FROM job").fetchone()
    assert row is not None
    assert str(row["status"]) == "PENDING"
    assert row["claimed_by"] is None


def test_every_restored_grant_requires_revalidation(restored: str, restored_admin_url: str) -> None:
    """Fail closed, because the data cannot answer the question.

    A grant revoked after the snapshot is live in the backup and revoked in the world. Nothing in
    the restored data distinguishes the two, so every restored grant is marked unusable until a
    person clears it.

    The flag is the point. An earlier version of `reconcile` only *listed* the grants and returned
    the count, which is a report rather than a control: the grants stayed usable and the "requires
    revalidation" statement lived in a runbook nobody reads during an incident.
    """
    grant_id = str(uuid.uuid4())
    with workspace_connection(restored, WS) as conn:
        project_id = conn.execute("SELECT id FROM project LIMIT 1").fetchone()

    with connect(restored_admin_url) as conn:
        conn.execute(
            """
            INSERT INTO execution_grant
                (id, workspace_id, project_id, environment, allowed_journey_versions,
                 allowed_policy_versions, action_budget, wall_time_budget_seconds, expires_at)
            VALUES (%s, %s, %s, 'staging', ARRAY['j1'], ARRAY['p1'], 10, 600,
                    now() + interval '1 day')
            """,
            (grant_id, WS, project_id["id"] if project_id else WS),
        )
        conn.commit()

    with connect(restored_admin_url) as conn:
        before = conn.execute(
            "SELECT revalidation_required FROM execution_grant WHERE id = %s", (grant_id,)
        ).fetchone()
        assert before is not None and before["revalidation_required"] is False

        report = restore.reconcile(conn, operator="drill")
        conn.commit()

    assert grant_id in report.grants_requiring_revalidation

    with connect(restored_admin_url) as conn:
        after = conn.execute(
            "SELECT revalidation_required, revalidated_at, revalidated_by "
            "  FROM execution_grant WHERE id = %s",
            (grant_id,),
        ).fetchone()
    assert after is not None
    assert after["revalidation_required"] is True
    # Cleared, not merely overwritten: a stale "revalidated by Alice last year" beside a fresh
    # requirement would read as though somebody had already looked.
    assert after["revalidated_at"] is None
    assert after["revalidated_by"] is None


def test_reconciliation_changes_no_terminal_run(restored: str, restored_admin_url: str) -> None:
    with workspace_connection(restored, WS) as conn:
        before = conn.execute(
            "SELECT id, status, outcome FROM run WHERE status = 'COMPLETED'"
        ).fetchone()
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
    with workspace_connection(restored, WS) as conn:
        after = conn.execute(
            "SELECT id, status, outcome FROM run WHERE status = 'COMPLETED'"
        ).fetchone()
    assert before is not None and after is not None
    # A restore that "tidied" a run into a better outcome would be a false PASS produced by an
    # operator holding a backup.
    assert dict(before) == dict(after)


def test_reconciliation_changes_no_evidence(restored: str, restored_admin_url: str) -> None:
    with workspace_connection(restored, WS) as conn:
        before = conn.execute(
            "SELECT sequence, payload_digest, previous_event_hash FROM canonical_event "
            "ORDER BY sequence"
        ).fetchall()
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
    with workspace_connection(restored, WS) as conn:
        after = conn.execute(
            "SELECT sequence, payload_digest, previous_event_hash FROM canonical_event "
            "ORDER BY sequence"
        ).fetchall()
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_reconciliation_is_recorded_and_refuses_to_run_twice(
    restored: str, restored_admin_url: str
) -> None:
    """Once is a recovery step; twice is an outage.

    A second pass would fence leases granted legitimately since the first, and quarantine runners an
    operator had just reset.
    """
    with connect(restored_admin_url) as conn:
        restore.reconcile(conn, operator="drill")
        row = conn.execute(
            "SELECT action, detail FROM global_audit_event WHERE action = %s",
            (restore.RECONCILIATION_MARKER,),
        ).fetchone()
        assert row is not None
        assert int(dict(row["detail"])["sessionsRevoked"]) == 1

        with pytest.raises(restore.RestoreError, match="already been reconciled"):
            restore.reconcile(conn, operator="drill")


def test_tenant_isolation_survives_the_restore(restored: str) -> None:
    """Row-level security is schema, so it comes back with the schema — asserted, not assumed."""
    other = str(uuid.uuid4())
    with unscoped_connection(restored) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Other')", (other,))
    with workspace_connection(restored, other) as conn:
        rows = conn.execute("SELECT count(*) AS n FROM canonical_event").fetchone()
    assert rows and int(rows["n"]) == 0


# --------------------------------------------------------------------------------------------------
# Schema compatibility
# --------------------------------------------------------------------------------------------------


def test_a_restored_database_reports_the_schema_it_was_written_by(restored: str) -> None:
    expected = tuple(sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql")))
    with unscoped_connection(restored) as conn:
        state = restore.schema_state(conn)
        compatible, detail = restore.restore_is_forward_compatible(conn, expected=expected)
    assert state.latest == expected[-1]
    assert compatible
    assert "matches the current tree exactly" in detail


def test_the_compatibility_helper_counts_a_gap_in_the_ledger(restored: str) -> None:
    """A ledger-level test, and it says so.

    An earlier version of this was called "an older release is migrated forward" and it was not
    that test: deleting one ledger row leaves every schema change applied, including the newest, so
    it exercised the helper's arithmetic and nothing else. The real forward-migration drill builds a
    database at the previous migration and runs the migrator against it --
    `tests/integration/test_forward_migration_and_interruption.py`.

    What this *does* cover is the case that drill cannot reach: a gap in the middle of the ledger,
    which is what a hand-repaired database looks like. The helper must count it as behind rather
    than as compatible, because the migrator will try to apply it.
    """
    expected = tuple(sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql")))
    gap = expected[len(expected) // 2]
    with unscoped_connection(restored) as conn:
        conn.execute("DELETE FROM schema_migration WHERE name = %s", (gap,))
    with unscoped_connection(restored) as conn:
        compatible, detail = restore.restore_is_forward_compatible(conn, expected=expected)
    assert compatible
    assert "1 migration(s) behind" in detail
    assert gap in detail


def test_a_database_written_by_a_newer_release_is_refused(restored: str) -> None:
    """Forward only.

    A code rollback does not reverse a data migration, and old code reading a newer schema treats
    columns it does not know about as absent. The recovery is to bring the code forward.
    """
    with unscoped_connection(restored) as conn:
        conn.execute(
            "INSERT INTO schema_migration (name) VALUES (%s)", ("9999_from_the_future.sql",)
        )
        expected = tuple(sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql")))
        compatible, detail = restore.restore_is_forward_compatible(conn, expected=expected)
    assert not compatible
    assert "written by a newer release" in detail
    assert "bring the code forward" in detail


def test_a_previous_reconciliation_in_the_backup_does_not_block_the_next_restore(
    restored: str, restored_admin_url: str
) -> None:
    """The trap that springs years later, during the incident it would make worse.

    Reconcile production once and that audit row is in every backup taken afterwards. A check for
    "has this database ever been reconciled" therefore refuses the *next* real restore -- leaving
    the target holding live sessions, granted leases and unredeemed enrollment tokens, which is the
    exact state reconciliation exists to eliminate.

    So the guarantee is scoped to a restore, not to a database. This simulates the row a previous
    restore left behind and then reconciles a genuinely new one.
    """
    with connect(restored_admin_url) as conn:
        conn.execute(
            """
            INSERT INTO global_audit_event
                (actor_user, actor_service, action, target_kind, outcome, detail)
            VALUES (NULL, 'an-earlier-operator', %s, 'database', 'ALLOWED',
                    %s::jsonb)
            """,
            (restore.RECONCILIATION_MARKER, '{"restoreId": "a-restore-from-two-years-ago"}'),
        )
        conn.commit()

    with connect(restored_admin_url) as conn:
        report = restore.reconcile(conn, operator="drill", restore_id="todays-archive")
        conn.commit()
    assert report.sessions_revoked >= 1

    # And the same restore, twice, is still refused: that is the property being preserved.
    with connect(restored_admin_url) as conn:
        with pytest.raises(restore.RestoreError, match="todays-archive"):
            restore.reconcile(conn, operator="drill", restore_id="todays-archive")


def test_a_restored_grant_cannot_run_a_schedule_until_a_person_clears_it(
    restored: str, restored_admin_url: str
) -> None:
    """The control, exercised where dispatch actually happens.

    `schedules.admit_occurrence` reads `revalidation_required` from the row rather than from the
    grant object it is handed, which is what makes this enforcement rather than documentation: a
    caller holding a grant built before the restore would present `revalidation_required=False` no
    matter what the database says.
    """
    from accessforge_domain.authority import ExecutionGrant

    grant = ExecutionGrant(
        grant_id=str(uuid.uuid4()),
        workspace_id=WS,
        project_id=str(uuid.uuid4()),
        environment="staging",
        allowed_journey_version_ids=frozenset({"j1"}),
        allowed_policy_version_ids=frozenset({"p1"}),
        permitted_effects=frozenset(),
        action_budget=10,
        wall_time_budget_seconds=600,
        expires_at="2027-01-01T00:00:00Z",
        revision=1,
    )
    grant.check_usable(now="2026-09-10T00:00:00Z")

    restored_grant = ExecutionGrant(
        grant_id=grant.grant_id,
        workspace_id=grant.workspace_id,
        project_id=grant.project_id,
        environment=grant.environment,
        allowed_journey_version_ids=grant.allowed_journey_version_ids,
        allowed_policy_version_ids=grant.allowed_policy_version_ids,
        permitted_effects=grant.permitted_effects,
        action_budget=grant.action_budget,
        wall_time_budget_seconds=grant.wall_time_budget_seconds,
        expires_at=grant.expires_at,
        revision=grant.revision,
        revalidation_required=True,
    )
    with pytest.raises(AuthorityError, match="restored from a backup"):
        restored_grant.check_usable(now="2026-09-10T00:00:00Z")
