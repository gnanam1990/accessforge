"""Desktop lease admission, cancellation, quarantine and reset, against real PostgreSQL.

The negative tests the module prompt lists, executed here rather than described: two supervisors
race on one session; a stale epoch submits a stop acknowledgement; an offline runner acts after
expiry; a result is lost; a profile lies; a reset fails; an old attempt sends late evidence. Plus
the one the prompt asks to be proved explicitly -- cancellation requested is not terminal CANCELLED.

Requirements: FR-004, FR-005, FR-014, FR-015, FR-021.
Invariants: INV-06, INV-07, INV-08, INV-09, INV-10, INV-13, INV-14.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.runners import (
    REQUIRED_PREFLIGHT_CHECKS,
    AmbiguityReason,
    PhysicalSession,
    PreflightCheck,
    PreflightResult,
    RunnerProfile,
)
from accessforge_domain.states import Condition, RunnerStatus
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    runners,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x07))
WS_OTHER = str(uuid.UUID(int=0x08))
OPERATOR = str(uuid.UUID(int=0x0A))
MANIFEST = digest({"m": "07"})

PROFILE = RunnerProfile(
    platform="darwin",
    reader_name="VoiceOver",
    reader_version="10.0",
    browser_name="Safari",
    browser_version="18.2",
    locale="en-US",
    keyboard_layout="ANSI",
)
SESSION = PhysicalSession(
    device_id="desk-01", platform="darwin", interactive_session_id="100005", console=True
)
SECOND_SESSION = PhysicalSession(
    device_id="desk-02", platform="darwin", interactive_session_id="100007", console=True
)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


# --- helpers -------------------------------------------------------------------------------------


def _checks(**overrides: Condition) -> dict[PreflightCheck, Condition]:
    checks = dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, Condition.TRUE)
    for name, value in overrides.items():
        checks[PreflightCheck(name)] = value
    return checks


def _preflight(
    *,
    session: PhysicalSession = SESSION,
    profile: RunnerProfile = PROFILE,
    reader_version: str | None = None,
    checks: dict[PreflightCheck, Condition] | None = None,
) -> PreflightResult:
    return PreflightResult(
        runner_profile_digest=profile.digest,
        environment_config_digest=digest({"env": "local"}),
        manifest_digest=MANIFEST,
        observed_reader_version=reader_version or profile.reader_version,
        observed_browser_version=profile.browser_version,
        observed_locale=profile.locale,
        observed_keyboard_layout=profile.keyboard_layout,
        desktop_session_key=session.key,
        observed_at="2026-09-10T12:00:00.000000Z",
        checks=checks if checks is not None else _checks(),
    )


def _enroll(
    url: str,
    *,
    workspace: str = WS,
    session: PhysicalSession = SESSION,
    profile: RunnerProfile = PROFILE,
    name: str = "mac-mini-01",
) -> str:
    with workspace_connection(url, workspace) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=workspace, created_by=OPERATOR)
        enrolled = runners.enroll_runner(
            conn,
            workspace_id=workspace,
            token=token.token,
            name=name,
            session=session,
            profile=profile,
        )
    return enrolled.runner_id


def _make_ready(
    url: str,
    runner_id: str,
    *,
    workspace: str = WS,
    session: PhysicalSession = SESSION,
    profile: RunnerProfile = PROFILE,
) -> None:
    with workspace_connection(url, workspace) as conn:
        record = runners.record_preflight(
            conn,
            workspace_id=workspace,
            runner_id=runner_id,
            result=_preflight(session=session, profile=profile),
        )
    assert record.runner_status is RunnerStatus.READY, record.refusal_summary


def _ready_runner(
    url: str,
    *,
    workspace: str = WS,
    session: PhysicalSession = SESSION,
    profile: RunnerProfile = PROFILE,
    name: str = "mac-mini-01",
) -> str:
    runner_id = _enroll(url, workspace=workspace, session=session, profile=profile, name=name)
    _make_ready(url, runner_id, workspace=workspace, session=session, profile=profile)
    return runner_id


def _run_and_attempt(url: str, *, workspace: str = WS, epoch: int = 1) -> tuple[str, str]:
    with workspace_connection(url, workspace) as conn:
        run_id = runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(
            conn, run_id=run_id, workspace_id=workspace, lease_epoch=epoch
        )
    return run_id, attempt_id


def _status(url: str, runner_id: str, *, workspace: str = WS) -> tuple[str, str | None]:
    with workspace_connection(url, workspace) as conn:
        row = conn.execute(
            "SELECT status, quarantine_reason FROM runner WHERE id = %s", (runner_id,)
        ).fetchone()
    assert row is not None
    return str(row["status"]), row["quarantine_reason"]


# --- enrollment ----------------------------------------------------------------------------------


def test_a_new_runner_is_never_ready(db: str) -> None:
    """Enrollment establishes identity. Readiness is a separate conclusion from evidence."""
    runner_id = _enroll(db)
    assert _status(db, runner_id) == (RunnerStatus.PREFLIGHT_REQUIRED, None)


def test_an_enrollment_token_works_exactly_once(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS, created_by=OPERATOR)
        runners.enroll_runner(
            conn,
            workspace_id=WS,
            token=token.token,
            name="first",
            session=SESSION,
            profile=PROFILE,
        )
        with pytest.raises(runners.RunnerError, match="already redeemed"):
            runners.enroll_runner(
                conn,
                workspace_id=WS,
                token=token.token,
                name="second",
                session=SECOND_SESSION,
                profile=PROFILE,
            )


def test_an_expired_enrollment_token_is_refused(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(
            conn,
            workspace_id=WS,
            created_by=OPERATOR,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        with pytest.raises(runners.RunnerError, match="expired"):
            runners.enroll_runner(
                conn,
                workspace_id=WS,
                token=token.token,
                name="late",
                session=SESSION,
                profile=PROFILE,
                now="2026-09-10T12:05:00.000000Z",
            )


def test_the_raw_enrollment_token_is_never_stored(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS, created_by=OPERATOR)
        stored = conn.execute(
            "SELECT token_digest FROM runner_enrollment_token WHERE id = %s", (token.token_id,)
        ).fetchone()
    assert stored is not None
    assert token.token not in str(stored["token_digest"])
    assert len(str(stored["token_digest"])) == 64


def test_a_token_from_another_workspace_cannot_enroll_here(db: str) -> None:
    """INV-07. Enrollment credentials are workspace bound, not bearer tokens for the cluster."""
    with workspace_connection(db, WS_OTHER) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS_OTHER, created_by=OPERATOR)
    with workspace_connection(db, WS) as conn, pytest.raises(runners.RunnerError):
        runners.enroll_runner(
            conn,
            workspace_id=WS,
            token=token.token,
            name="thief",
            session=SESSION,
            profile=PROFILE,
        )


def test_one_physical_desktop_cannot_be_enrolled_twice(db: str) -> None:
    """Two registrations for one screen would each believe they could be leased independently."""
    _enroll(db)
    with pytest.raises(runners.RunnerError, match="already enrolled"):
        _enroll(db, name="duplicate")


def test_a_revoked_registration_frees_the_desktop_for_re_enrollment(db: str) -> None:
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        runners.revoke_runner(conn, runner_id=runner_id)
    assert _enroll(db, name="replacement") != runner_id


def test_a_runner_holding_an_active_lease_cannot_be_revoked(db: str) -> None:
    """Found by mutation, not by review.

    Removing the advisory lock left all 53 tests passing, which said the lock was proving nothing.
    The one case it *does* cover is two runner rows for one desktop -- reachable only by revoking a
    runner mid-lease and re-enrolling the same screen, because the per-runner row lock then
    serializes nothing. Rather than keep a lock justified by a window, the window is closed: a
    runner holding a live lease is not revocable.
    """
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
    with (
        workspace_connection(db, WS) as conn,
        pytest.raises(runners.RunnerError, match="active lease"),
    ):
        runners.revoke_runner(conn, runner_id=runner_id)


def test_a_runner_is_revocable_once_its_lease_is_released(db: str) -> None:
    """Allowed-path control, and the operator's actual route: stop the run, then retire the box."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)
        runners.revoke_runner(conn, runner_id=runner_id)
    assert _status(db, runner_id)[0] == RunnerStatus.OFFLINE


def test_a_quarantined_runner_can_still_be_retired(db: str) -> None:
    """A fenced desktop must not become unretirable. Quarantine releases the lease, so it is."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z")
        runners.revoke_runner(conn, runner_id=runner_id)


def test_a_profile_and_session_that_disagree_about_the_platform_are_refused(db: str) -> None:
    windows_profile = RunnerProfile(
        platform="win32",
        reader_name="NVDA",
        reader_version="2025.1",
        browser_name="Firefox",
        browser_version="140",
        locale="en-US",
        keyboard_layout="US",
    )
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS, created_by=OPERATOR)
        with pytest.raises(runners.RunnerError, match="disagree"):
            runners.enroll_runner(
                conn,
                workspace_id=WS,
                token=token.token,
                name="confused",
                session=SESSION,
                profile=windows_profile,
            )


# --- preflight -----------------------------------------------------------------------------------


def test_a_successful_preflight_makes_a_runner_ready(db: str) -> None:
    runner_id = _enroll(db)
    _make_ready(db, runner_id)
    assert _status(db, runner_id) == (RunnerStatus.READY, None)


def test_the_server_derives_success_rather_than_reading_it(db: str) -> None:
    """There is no column the runner controls. `successful` is computed from the checks."""
    runner_id = _enroll(db)
    _make_ready(db, runner_id)
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT successful, checks FROM runner_preflight WHERE runner_id = %s", (runner_id,)
        ).fetchone()
    assert row is not None
    assert row["successful"] is True
    assert set(dict(row["checks"])) == {str(c) for c in REQUIRED_PREFLIGHT_CHECKS}


def test_a_failed_preflight_quarantines_the_desktop(db: str) -> None:
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        record = runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=_preflight(checks=_checks(SCREEN_UNLOCKED=Condition.FALSE)),
        )
    assert record.successful is False
    assert _status(db, runner_id) == (
        RunnerStatus.QUARANTINED,
        "PREFLIGHT_FAILED",
    )


def test_a_lying_profile_is_caught_by_the_server_not_by_the_runners_own_check(db: str) -> None:
    """ "Profile lies" from the module prompt.

    Every check the runner reported is TRUE, including the one that claims its reader version
    matches its profile. The server holds the enrolled profile and compares the observed version
    itself, because a supervisor willing to misreport a version will also misreport whether the
    version matches.
    """
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        record = runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=_preflight(reader_version="9.1"),
        )
    assert record.successful is False
    assert "reader version drifted from '10.0' to '9.1'" in record.refusal_summary
    assert _status(db, runner_id) == (RunnerStatus.QUARANTINED, "PROFILE_DRIFTED")


def test_a_preflight_for_a_different_profile_digest_is_not_evidence_here(db: str) -> None:
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        record = runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=replace(_preflight(), runner_profile_digest=digest({"someone": "else"})),
        )
    assert record.successful is False
    assert "different runner profile digest" in record.refusal_summary


def test_a_revoked_runner_cannot_become_ready(db: str) -> None:
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        runners.revoke_runner(conn, runner_id=runner_id)
        with pytest.raises(runners.RunnerError, match="revoked"):
            runners.record_preflight(
                conn, workspace_id=WS, runner_id=runner_id, result=_preflight()
            )


# --- lease admission, INV-10 ---------------------------------------------------------------------


def test_a_ready_runner_admits_one_attempt(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
    assert lease.epoch == 1
    assert _status(db, runner_id) == (RunnerStatus.BUSY, None)


def test_a_second_attempt_on_one_desktop_is_refused(db: str) -> None:
    runner_id = _ready_runner(db)
    first_run, first_attempt = _run_and_attempt(db)
    second_run, second_attempt = _run_and_attempt(db, epoch=2)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=first_run,
            attempt_id=first_attempt,
        )
    with workspace_connection(db, WS) as conn, pytest.raises(runners.SessionBusy):
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=second_run,
            attempt_id=second_attempt,
        )


def test_two_supervisors_racing_on_one_desktop_produce_one_admitted_attempt(db: str) -> None:
    """The race, run concurrently against the real database rather than reasoned about.

    Two separate connections, two separate transactions, both calling admit_lease on one runner at
    the same moment. Exactly one must win; the loser must be refused rather than silently sharing
    the screen.
    """
    runner_id = _ready_runner(db)
    runs_and_attempts = [_run_and_attempt(db), _run_and_attempt(db, epoch=2)]

    def contend(pair: tuple[str, str]) -> str:
        run_id, attempt_id = pair
        try:
            with workspace_connection(db, WS) as conn:
                lease = runners.admit_lease(
                    conn,
                    workspace_id=WS,
                    runner_id=runner_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
            return f"admitted:{lease.epoch}"
        except (runners.SessionBusy, psycopg.errors.UniqueViolation) as exc:
            return f"refused:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contend, runs_and_attempts))

    admitted = [o for o in outcomes if o.startswith("admitted")]
    assert len(admitted) == 1, f"INV-10 violated: {outcomes}"

    with workspace_connection(db, WS) as conn:
        active = conn.execute(
            "SELECT count(*) AS n FROM desktop_lease WHERE released_at IS NULL"
        ).fetchone()
    assert active is not None and int(active["n"]) == 1


def test_exclusivity_is_enforced_by_the_database_not_by_this_module(db: str) -> None:
    """The guarantee is the partial unique index, proved by bypassing every check above it.

    The lease is inserted directly, so nothing in `admit_lease` -- not the status check, not the
    held-lease lookup, not the row lock -- is involved. Only the index can refuse it.

    This test is why the advisory lock that once sat alongside the index could be removed: mutation
    showed that deleting the index failed exactly this test and deleting the lock failed nothing,
    which is the difference between a guarantee and a decoration.
    """
    runner_id = _ready_runner(db)
    first_run, first_attempt = _run_and_attempt(db)
    second_run, second_attempt = _run_and_attempt(db, epoch=2)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=first_run,
            attempt_id=first_attempt,
        )

    with workspace_connection(db, WS) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch,
                 deadline_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now() + interval '5 minutes')
            """,
            (
                str(uuid.uuid4()),
                WS,
                runner_id,
                lease.session_key,
                second_run,
                second_attempt,
                99,
            ),
        )


def test_a_runner_that_is_not_ready_cannot_be_leased(db: str) -> None:
    runner_id = _enroll(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn, pytest.raises(runners.RunnerError, match="READY"):
        runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )


def test_a_quarantined_runner_cannot_be_leased(db: str) -> None:
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=_preflight(checks=_checks(READER_ACTIVE=Condition.FALSE)),
        )
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn, pytest.raises(runners.SessionBusy, match="quarant"):
        runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )


def test_epochs_are_monotonic_across_leases(db: str) -> None:
    """A reused epoch would make a stale supervisor's credentials valid again."""
    runner_id = _ready_runner(db)
    seen: list[int] = []
    for index in range(3):
        run_id, attempt_id = _run_and_attempt(db, epoch=index + 1)
        with workspace_connection(db, WS) as conn:
            lease = runners.admit_lease(
                conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
            )
            seen.append(lease.epoch)
            runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
            runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)
        _make_ready(db, runner_id)
    assert seen == [1, 2, 3]


def test_two_epochs_cannot_collide_on_one_runner(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
    other_run, other_attempt = _run_and_attempt(db, epoch=2)
    with workspace_connection(db, WS) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch, released_at,
                 release_reason, deadline_at)
            VALUES (%s, %s, %s, %s, %s, %s, 1, now(), 'COMPLETED', now())
            """,
            (str(uuid.uuid4()), WS, runner_id, SESSION.key, other_run, other_attempt),
        )


def test_two_desktops_run_concurrently(db: str) -> None:
    """Allowed-path control for INV-10: the constraint is per desktop, not a global mutex."""
    first = _ready_runner(db, session=SESSION, name="mac-01")
    second = _ready_runner(db, session=SECOND_SESSION, name="mac-02")
    for runner_id in (first, second):
        run_id, attempt_id = _run_and_attempt(db)
        with workspace_connection(db, WS) as conn:
            runners.admit_lease(
                conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
            )
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM desktop_lease WHERE released_at IS NULL"
        ).fetchone()
    assert row is not None and int(row["n"]) == 2


# --- heartbeat and stale epochs ------------------------------------------------------------------


def test_a_stale_epoch_cannot_extend_the_current_lease(db: str) -> None:
    """The epoch is a parameter, not read from the row: otherwise a superseded supervisor could
    refresh the lease of the one that replaced it."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        with pytest.raises(runners.RunnerError, match="superseded"):
            runners.heartbeat_lease(conn, lease_id=lease.lease_id, epoch=lease.epoch - 1)


def test_a_heartbeat_extends_the_deadline(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            now="2026-09-10T12:00:00.000000Z",
        )
        extended = runners.heartbeat_lease(
            conn,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            now="2026-09-10T12:02:00.000000Z",
        )
    assert extended > lease.deadline_at


# --- expiry fences rather than reclaims ----------------------------------------------------------


def test_an_expired_lease_quarantines_the_desktop_instead_of_reassigning_it(db: str) -> None:
    """The central honesty requirement of this module.

    A heartbeat stopping arriving means the network is down, or the supervisor is wedged, or the
    machine is asleep. None of those is proof that nothing is typing. The desktop is fenced, not
    handed to the next run.
    """
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        fenced = runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z")
    assert len(fenced) == 1
    assert _status(db, runner_id) == (
        RunnerStatus.QUARANTINED,
        "LEASE_EXPIRED_WITHOUT_STOP_PROOF",
    )

    next_run, next_attempt = _run_and_attempt(db, epoch=2)
    with workspace_connection(db, WS) as conn, pytest.raises(runners.SessionBusy, match="quarant"):
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=next_run,
            attempt_id=next_attempt,
        )


def test_an_unexpired_lease_is_left_alone_by_the_sweep(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=600,
            now="2026-09-10T12:00:00.000000Z",
        )
        assert runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z") == []
    assert _status(db, runner_id) == (RunnerStatus.BUSY, None)


def test_a_release_reason_records_that_expiry_was_not_a_stop(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z")
        row = conn.execute(
            "SELECT release_reason, stop_acknowledged_at FROM desktop_lease WHERE id = %s",
            (lease.lease_id,),
        ).fetchone()
    assert row is not None
    assert row["release_reason"] == "EXPIRED_WITHOUT_STOP_PROOF"
    assert row["stop_acknowledged_at"] is None, "expiry must never look like an acknowledgement"


# --- cancellation --------------------------------------------------------------------------------


def test_cancellation_requested_is_not_terminal_cancelled(db: str) -> None:
    """INV-01/INV-06/INV-13 as the prompt asks it to be proved.

    The lease records the request and stays active; the run stays non-terminal. Nothing here has
    established that the desktop stopped.
    """
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=7)
        row = conn.execute(
            "SELECT cancel_requested_at, cancellation_revision, stop_acknowledged_at, released_at "
            "FROM desktop_lease WHERE id = %s",
            (lease.lease_id,),
        ).fetchone()
        run_status = conn.execute("SELECT status FROM run WHERE id = %s", (run_id,)).fetchone()
    assert row is not None
    assert row["cancel_requested_at"] is not None
    assert int(row["cancellation_revision"]) == 7
    assert row["stop_acknowledged_at"] is None
    assert row["released_at"] is None, "a requested cancellation does not release the desktop"
    assert run_status is not None and str(run_status["status"]) != "CANCELLED"


def test_a_stop_acknowledgement_from_a_stale_epoch_is_refused(db: str) -> None:
    """ "Stale epoch submits stop acknowledgement" from the module prompt."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
        with pytest.raises(runners.RunnerError, match="superseded supervisor"):
            runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch + 1)


def test_a_stop_acknowledgement_with_an_action_in_flight_is_refused(db: str) -> None:
    """An acknowledgement while a keystroke is unresolved says the supervisor stopped asking, not
    that the keystroke did not land."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="TYPE_TEXT",
            origin="http://127.0.0.1:8081",
            text_value="Test Person",
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
        with pytest.raises(runners.RunnerError, match="still unresolved"):
            runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)


def test_a_stop_is_accepted_once_in_flight_actions_resolve(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        runners.record_action_result(conn, action_id=action_id, status="SUCCEEDED")
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)
    # Stopped cleanly, and still not READY: the browser is wherever the cancelled run left it.
    assert _status(db, runner_id) == (RunnerStatus.PREFLIGHT_REQUIRED, None)


def test_acknowledging_without_a_cancellation_is_refused(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        with pytest.raises(runners.RunnerError, match="nothing to acknowledge"):
            runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)


def test_a_never_leased_run_may_cancel_immediately(db: str) -> None:
    """The one case where CANCELLED is reachable without an acknowledgement: never admitted."""
    run_id, _ = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        assert runners.may_cancel_immediately(conn, run_id=run_id) is True


def test_a_run_that_ever_held_a_lease_may_not_cancel_immediately(db: str) -> None:
    """Even after the lease is released. Release is not proof that nothing happened."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.request_cancellation(conn, lease_id=lease.lease_id, cancellation_revision=1)
        runners.acknowledge_stop(conn, lease_id=lease.lease_id, epoch=lease.epoch)
        assert runners.may_cancel_immediately(conn, run_id=run_id) is False


# --- the action journal --------------------------------------------------------------------------


def test_intent_is_recorded_before_dispatch(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="ACTIVATE",
            origin="http://127.0.0.1:8081",
        )
        row = conn.execute(
            "SELECT intent_at, dispatched_at, result_at FROM runner_action WHERE id = %s",
            (action_id,),
        ).fetchone()
    assert row is not None
    assert row["intent_at"] is not None
    assert row["dispatched_at"] is None
    assert row["result_at"] is None


def test_an_action_cannot_be_dispatched_twice(db: str) -> None:
    """INV-09. The first dispatch may have taken effect, so the second is not a retry, it is a
    second press."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="ACTIVATE",
            origin="http://127.0.0.1:8081",
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        with pytest.raises(runners.RunnerError, match="already dispatched"):
            runners.mark_action_dispatched(conn, action_id=action_id)


def test_a_lost_result_becomes_ambiguous_and_quarantines_the_desktop(db: str) -> None:
    """ "Acknowledgement/result is lost" from the module prompt.

    The process asked to press a key may still be running and may still press it, so the desktop is
    fenced rather than reused.
    """
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="TYPE_TEXT",
            origin="http://127.0.0.1:8081",
            text_value="Test Person",
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        runners.mark_action_ambiguous(
            conn,
            action_id=action_id,
            reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
            detail="no ACTION_RESULT within the local timeout",
        )
        row = conn.execute(
            "SELECT result_status, ambiguity_reason FROM runner_action WHERE id = %s",
            (action_id,),
        ).fetchone()
        released = conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id = %s", (lease.lease_id,)
        ).fetchone()
    assert row is not None and row["result_status"] == "AMBIGUOUS"
    assert row["ambiguity_reason"] == "ACTION_RESULT_NEVER_ARRIVED"
    assert released is not None and released["release_reason"] == "AMBIGUOUS_ACTION"
    assert _status(db, runner_id) == (RunnerStatus.QUARANTINED, "AMBIGUOUS_ACTION")


def test_an_ambiguous_result_cannot_be_recorded_as_an_ordinary_one(db: str) -> None:
    """Otherwise an unknown would pass as a known in every query filtering on status."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )
        with pytest.raises(runners.RunnerError, match="mark_action_ambiguous"):
            runners.record_action_result(conn, action_id=action_id, status="AMBIGUOUS")


def test_a_result_is_recorded_once(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )
        runners.record_action_result(conn, action_id=action_id, status="SUCCEEDED")
        with pytest.raises(runners.RunnerError, match="recorded once"):
            runners.record_action_result(conn, action_id=action_id, status="FAILED")


def test_an_old_attempt_cannot_reuse_an_action_sequence(db: str) -> None:
    """ "Old attempt sends late evidence." The unique key is (run, attempt, sequence), so a late
    record from a superseded attempt cannot overwrite or fork the current one's journal."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            runners.record_action_intent(
                conn,
                workspace_id=WS,
                lease_id=lease.lease_id,
                run_id=run_id,
                attempt_id=attempt_id,
                epoch=lease.epoch,
                action_sequence=1,
                action="PREVIOUS",
                origin="http://127.0.0.1:8081",
            )


# --- reset ---------------------------------------------------------------------------------------


def test_a_failed_reset_leaves_the_desktop_quarantined(db: str) -> None:
    """ "Reset fails." Failing to prove the old actor cannot act is not proof that it can't."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z")
        outcome = runners.reset_runner(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            requested_by=OPERATOR,
            succeeded=False,
            proof={"supervisorProcessGone": False, "readerRestarted": False},
        )
    assert outcome.succeeded is False
    assert _status(db, runner_id) == (RunnerStatus.QUARANTINED, "RESET_FAILED")


def test_a_successful_reset_requires_a_fresh_preflight_before_work(db: str) -> None:
    """Reset proves the old session is dead. It does not prove the new one works."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=run_id,
            attempt_id=attempt_id,
            ttl_seconds=60,
            now="2026-09-10T12:00:00.000000Z",
        )
        runners.fence_expired_leases(conn, now="2026-09-10T12:05:00.000000Z")
        outcome = runners.reset_runner(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            requested_by=OPERATOR,
            succeeded=True,
            proof={"supervisorProcessGone": True, "readerRestarted": True},
        )
    assert outcome.runner_status is RunnerStatus.PREFLIGHT_REQUIRED

    next_run, next_attempt = _run_and_attempt(db, epoch=2)
    with workspace_connection(db, WS) as conn, pytest.raises(runners.RunnerError, match="READY"):
        runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=next_run,
            attempt_id=next_attempt,
        )

    _make_ready(db, runner_id)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            run_id=next_run,
            attempt_id=next_attempt,
        )
    assert lease.epoch > outcome.fenced_epoch, "the new lease is past the fenced epoch"


def test_a_reset_records_its_local_impact_warning(db: str) -> None:
    """An operator resetting a machine someone may be sitting at is entitled to have been told."""
    runner_id = _ready_runner(db)
    with workspace_connection(db, WS) as conn:
        outcome = runners.reset_runner(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            requested_by=OPERATOR,
            succeeded=True,
            proof={},
        )
        row = conn.execute(
            "SELECT local_impact_warning FROM runner_reset WHERE id = %s", (outcome.reset_id,)
        ).fetchone()
    assert row is not None
    warning = str(row["local_impact_warning"])
    assert "unsaved work" in warning
    assert "does not kill processes outside" in warning


def test_a_reset_supersedes_any_lease_still_open(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.reset_runner(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            requested_by=OPERATOR,
            succeeded=True,
            proof={},
        )
        row = conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id = %s", (lease.lease_id,)
        ).fetchone()
    assert row is not None and row["release_reason"] == "SUPERSEDED_BY_RESET"


def test_inspection_shows_an_operator_what_is_in_flight(db: str) -> None:
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="ACTIVATE",
            origin="http://127.0.0.1:8081",
        )
        report = runners.inspect_runner(conn, runner_id=runner_id)
    assert report["activeLease"] is not None
    assert len(report["unresolvedActions"]) == 1
    assert report["unresolvedActions"][0]["action"] == "ACTIVATE"
    assert "unsaved work" in report["localImpactWarning"]


# --- capability matching and backpressure --------------------------------------------------------


def test_an_unsupported_reader_returns_a_visible_reason(db: str) -> None:
    """Never a silent fallback to a browser without a screen reader (INV-02)."""
    _ready_runner(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerUnavailable) as caught:
            runners.match_runners(conn, platform="win32", reader_name="NVDA")
    assert caught.value.reason == "NO_RUNNER_FOR_READER"
    assert "will not be run in a browser without a screen reader" in str(caught.value)


def test_an_unsupported_reader_version_is_distinguished_from_an_unsupported_reader(db: str) -> None:
    _ready_runner(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerUnavailable) as caught:
            runners.match_runners(
                conn, platform="darwin", reader_name="VoiceOver", reader_version="9.1"
            )
    assert caught.value.reason == "NO_RUNNER_FOR_READER_VERSION"


def test_all_matching_runners_quarantined_is_its_own_reason(db: str) -> None:
    """It leads an operator to a reset, where "no runner enrolled" leads them to enrollment."""
    runner_id = _enroll(db)
    with workspace_connection(db, WS) as conn:
        runners.record_preflight(
            conn,
            workspace_id=WS,
            runner_id=runner_id,
            result=_preflight(checks=_checks(SPEECH_CAPTURE_WORKING=Condition.FALSE)),
        )
        with pytest.raises(runners.RunnerUnavailable) as caught:
            runners.match_runners(conn, platform="darwin", reader_name="VoiceOver")
    assert caught.value.reason == "ALL_MATCHING_RUNNERS_QUARANTINED"
    assert "PREFLIGHT_FAILED" in str(caught.value)


def test_a_matching_ready_runner_is_returned(db: str) -> None:
    """Allowed-path control for the matcher."""
    runner_id = _ready_runner(db)
    with workspace_connection(db, WS) as conn:
        matched = runners.match_runners(
            conn, platform="darwin", reader_name="VoiceOver", reader_version="10.0"
        )
    assert [str(r["id"]) for r in matched] == [runner_id]


def test_the_queue_is_bounded(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        for _ in range(3):
            runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        assert runners.assert_queue_capacity(conn, limit=10) == 3
        with pytest.raises(runners.QueueFull, match="cannot drain"):
            runners.assert_queue_capacity(conn, limit=3)


# --- tenancy -------------------------------------------------------------------------------------


def test_runners_leases_and_actions_are_workspace_isolated(db: str) -> None:
    """INV-07 covers runners as well as rows."""
    runner_id = _ready_runner(db)
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease.lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            epoch=lease.epoch,
            action_sequence=1,
            action="NEXT",
            origin="http://127.0.0.1:8081",
        )

    with workspace_connection(db, WS_OTHER) as conn:
        for table in ("runner", "desktop_lease", "runner_action", "runner_preflight"):
            rows = conn.execute(f"SELECT 1 FROM {table}").fetchall()  # noqa: S608 - fixed list
            assert rows == [], f"{table} leaked across tenants"
        with pytest.raises(runners.RunnerError, match="no such runner"):
            runners.inspect_runner(conn, runner_id=runner_id)


def test_another_tenant_cannot_lease_this_tenants_desktop(db: str) -> None:
    runner_id = _ready_runner(db)
    with workspace_connection(db, WS_OTHER) as conn:
        other_run = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
        other_attempt = runs.start_attempt(
            conn, run_id=other_run, workspace_id=WS_OTHER, lease_epoch=1
        )
        with pytest.raises(runners.RunnerError, match="no such runner"):
            runners.admit_lease(
                conn,
                workspace_id=WS_OTHER,
                runner_id=runner_id,
                run_id=other_run,
                attempt_id=other_attempt,
            )
