"""Where a runner's knowledge meets the run reducers.

The prompt asks for one thing to be proved explicitly: "Prove cancellation requested is not
terminal CANCELLED." These tests do that by trying every route to CANCELLED that is not a real stop
proof, and showing each one refused.

Requirements: FR-005, FR-014. Invariants: INV-01, INV-02, INV-06, INV-09, INV-11, INV-13.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from accessforge_domain import reducers
from accessforge_domain.canonical import digest
from accessforge_domain.runners import (
    REQUIRED_PREFLIGHT_CHECKS,
    AmbiguityReason,
    PhysicalSession,
    PreflightResult,
    RunnerProfile,
)
from accessforge_domain.states import Condition, Outcome, RunnerStatus, RunStatus
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    runners,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x70))
OPERATOR = str(uuid.UUID(int=0x7A))
MANIFEST = digest({"m": "07-terminal"})
ORIGIN = "http://127.0.0.1:8081"

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
    device_id="desk-70", platform="darwin", interactive_session_id="100070", console=True
)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (WS, "A"))
    yield test_database_url


def _preflight() -> PreflightResult:
    return PreflightResult(
        runner_profile_digest=PROFILE.digest,
        environment_config_digest=digest({"env": "local"}),
        manifest_digest=MANIFEST,
        observed_reader_version=PROFILE.reader_version,
        observed_browser_version=PROFILE.browser_version,
        observed_locale=PROFILE.locale,
        observed_keyboard_layout=PROFILE.keyboard_layout,
        desktop_session_key=SESSION.key,
        observed_at="2026-09-10T12:00:00.000000Z",
        checks=dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, Condition.TRUE),
    )


def _ready_runner(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        token = runners.issue_enrollment_token(conn, workspace_id=WS, created_by=OPERATOR)
        enrolled = runners.enroll_runner(
            conn,
            workspace_id=WS,
            token=token.token,
            name="mac-70",
            session=SESSION,
            profile=PROFILE,
        )
        record = runners.record_preflight(
            conn, workspace_id=WS, runner_id=enrolled.runner_id, result=_preflight()
        )
    assert record.runner_status is RunnerStatus.READY
    return enrolled.runner_id


def _queued_run(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        return runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)


def _running_with_lease(db: str) -> tuple[str, str, str, int]:
    """A run that is RUNNING on a real lease, with one action admitted."""
    runner_id = _ready_runner(db)
    run_id = _queued_run(db)
    with workspace_connection(db, WS) as conn:
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        lease = runners.admit_lease(
            conn, workspace_id=WS, runner_id=runner_id, run_id=run_id, attempt_id=attempt_id
        )
        # admit_lease already moved the run QUEUED -> LEASED and recorded the epoch.
        state = runs.load_run(conn, run_id=run_id).state
        assert state.lease_epoch == lease.epoch
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.progress(s, expected_revision=s.revision),
            operation_id=str(uuid.uuid4()),
            topic="run.running",
            expected_revision=state.revision,
            actor_service="test",
        )
    return runner_id, lease.lease_id, run_id, lease.epoch


def _state(db: str, run_id: str) -> tuple[RunStatus, Outcome, int]:
    with workspace_connection(db, WS) as conn:
        s = runs.load_run(conn, run_id=run_id).state
    return s.status, s.outcome, s.revision


def _request_cancel(db: str, run_id: str, lease_id: str | None) -> None:
    with workspace_connection(db, WS) as conn:
        state = runs.load_run(conn, run_id=run_id).state
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.request_cancellation(
                s, requested_at="2026-09-10T12:05:00.000000Z", expected_revision=s.revision
            ),
            operation_id=str(uuid.uuid4()),
            topic="run.cancellation_requested",
            expected_revision=state.revision,
            actor_service="test",
        )
        if lease_id is not None:
            runners.request_cancellation(
                conn, lease_id=lease_id, cancellation_revision=state.revision
            )


# --- cancellation requested is not terminal CANCELLED --------------------------------------------


def test_a_requested_cancellation_leaves_the_run_nonterminal(db: str) -> None:
    _, lease_id, run_id, _ = _running_with_lease(db)
    _request_cancel(db, run_id, lease_id)
    status, outcome, _ = _state(db, run_id)
    assert status is RunStatus.RUNNING, "the run is still running; only new work is fenced"
    assert outcome is Outcome.NOT_EVALUATED


def test_terminal_cancelled_is_refused_without_a_stop_acknowledgement(db: str) -> None:
    """The central refusal. The desktop may still be typing, and CANCELLED would say it is not."""
    _, lease_id, run_id, _ = _running_with_lease(db)
    _request_cancel(db, run_id, lease_id)
    _, _, revision = _state(db, run_id)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerError, match="no stop has been acknowledged"):
            runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)
    assert _state(db, run_id)[0] is RunStatus.RUNNING


def test_an_expired_lease_is_not_a_route_to_cancelled(db: str) -> None:
    """CONTRACTS: "Lease timeout alone is not a verified stop." A silent supervisor ends
    INTERRUPTED, not CANCELLED, and the difference is whether anything proved it stopped."""
    _, lease_id, run_id, _ = _running_with_lease(db)
    _request_cancel(db, run_id, lease_id)
    with workspace_connection(db, WS) as conn:
        runners.fence_expired_leases(conn, now="2026-09-10T23:00:00.000000Z")
    _, _, revision = _state(db, run_id)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerError, match="not proof"):
            runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)


def test_cancelled_is_refused_while_an_action_is_unresolved(db: str) -> None:
    _, lease_id, run_id, epoch = _running_with_lease(db)
    with workspace_connection(db, WS) as conn:
        attempt = conn.execute(
            "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
        ).fetchone()
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=str(attempt["attempt_id"]),
            epoch=epoch,
            action_sequence=1,
            action="TYPE_TEXT",
            origin=ORIGIN,
            text_value="Test Person",
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
    _request_cancel(db, run_id, lease_id)

    # The lease-level acknowledgement is refused first, which is the same guarantee one layer down.
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerError, match="still unresolved"):
            runners.acknowledge_stop(conn, lease_id=lease_id, epoch=epoch)

    _, _, revision = _state(db, run_id)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerError, match="no stop has been acknowledged"):
            runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)


def test_an_acknowledged_stop_admits_terminal_cancelled(db: str) -> None:
    """The allowed path. Without it every refusal above could be a function that refuses all."""
    _, lease_id, run_id, epoch = _running_with_lease(db)
    _request_cancel(db, run_id, lease_id)
    with workspace_connection(db, WS) as conn:
        runners.acknowledge_stop(conn, lease_id=lease_id, epoch=epoch)
        state = runs.load_run(conn, run_id=run_id).state
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.acknowledge_stop(
                s,
                acknowledged_at="2026-09-10T12:06:00.000000Z",
                epoch=epoch,
                expected_revision=s.revision,
            ),
            operation_id=str(uuid.uuid4()),
            topic="run.stop_acknowledged",
            expected_revision=state.revision,
            actor_service="test",
        )
        # Read from this connection, not through a fresh one: the transitions above are in this
        # open transaction and a nested connection would not see them.
        revision = runs.load_run(conn, run_id=run_id).state.revision
        runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)

    status, outcome, _ = _state(db, run_id)
    assert status is RunStatus.CANCELLED
    assert outcome is Outcome.INCONCLUSIVE, (
        "execution had begun, so nothing was established about accessibility"
    )


def test_a_never_leased_run_cancels_immediately(db: str) -> None:
    """Provably never admitted to a desktop, so provably unable to have typed anything."""
    run_id = _queued_run(db)
    _request_cancel(db, run_id, None)
    _, _, revision = _state(db, run_id)
    with workspace_connection(db, WS) as conn:
        runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)
    status, outcome, _ = _state(db, run_id)
    assert status is RunStatus.CANCELLED
    assert outcome is Outcome.NOT_EVALUATED, "nothing ran, so there is nothing to call inconclusive"


def test_cancelling_without_a_request_is_refused(db: str) -> None:
    run_id = _queued_run(db)
    _, _, revision = _state(db, run_id)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(runners.RunnerError, match="never requested"):
            runners.terminalize_cancellation(conn, run_id=run_id, expected_revision=revision)


# --- ambiguity ends INTERRUPTED ------------------------------------------------------------------


def test_an_ambiguous_action_ends_the_run_interrupted_and_inconclusive(db: str) -> None:
    """INV-02. Infrastructure ambiguity is not a reproduced accessibility defect."""
    runner_id, lease_id, run_id, epoch = _running_with_lease(db)
    with workspace_connection(db, WS) as conn:
        attempt = conn.execute(
            "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
        ).fetchone()
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=str(attempt["attempt_id"]),
            epoch=epoch,
            action_sequence=1,
            action="ACTIVATE",
            origin=ORIGIN,
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        _, _, revision = _state(db, run_id)
        runners.terminalize_ambiguous_attempt(
            conn,
            run_id=run_id,
            action_id=action_id,
            reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
            expected_revision=revision,
            detail="no ACTION_RESULT within the local timeout",
        )

    status, outcome, _ = _state(db, run_id)
    assert status is RunStatus.INTERRUPTED
    assert outcome is Outcome.INCONCLUSIVE

    with workspace_connection(db, WS) as conn:
        state = runs.load_run(conn, run_id=run_id).state
    assert state.ambiguity_reason == "ACTION_RESULT_NEVER_ARRIVED"
    assert state.quarantined is True


def test_the_run_and_its_desktop_are_fenced_in_one_transaction(db: str) -> None:
    """A committed quarantine beside a run still reading RUNNING would tell an operator the desktop
    is unsafe and the work is fine. Those are the same fact."""
    runner_id, lease_id, run_id, epoch = _running_with_lease(db)
    with workspace_connection(db, WS) as conn:
        attempt = conn.execute(
            "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
        ).fetchone()
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=str(attempt["attempt_id"]),
            epoch=epoch,
            action_sequence=1,
            action="ACTIVATE",
            origin=ORIGIN,
        )
        runners.mark_action_dispatched(conn, action_id=action_id)
        _, _, revision = _state(db, run_id)
        runners.terminalize_ambiguous_attempt(
            conn,
            run_id=run_id,
            action_id=action_id,
            reason=AmbiguityReason.SUPERVISOR_CRASHED_AFTER_INTENT,
            expected_revision=revision,
        )

    with workspace_connection(db, WS) as conn:
        runner = conn.execute(
            "SELECT status, quarantine_reason FROM runner WHERE id = %s", (runner_id,)
        ).fetchone()
        lease = conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id = %s", (lease_id,)
        ).fetchone()
    assert str(runner["status"]) == RunnerStatus.QUARANTINED
    assert runner["quarantine_reason"] == "AMBIGUOUS_ACTION"
    assert lease["release_reason"] == "AMBIGUOUS_ACTION"
    assert _state(db, run_id)[0] is RunStatus.INTERRUPTED


def test_a_stale_revision_refuses_the_interruption(db: str) -> None:
    """The decision was made about a state that no longer exists."""
    _, lease_id, run_id, epoch = _running_with_lease(db)
    with workspace_connection(db, WS) as conn:
        attempt = conn.execute(
            "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
        ).fetchone()
        action_id = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=str(attempt["attempt_id"]),
            epoch=epoch,
            action_sequence=1,
            action="NEXT",
            origin=ORIGIN,
        )
        with pytest.raises(runs.StaleRevision):
            runners.terminalize_ambiguous_attempt(
                conn,
                run_id=run_id,
                action_id=action_id,
                reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
                expected_revision=9999,
            )


def test_a_terminal_run_cannot_be_interrupted_again(db: str) -> None:
    """INV-11: terminal records are immutable, and corrections are append-only links."""
    _, lease_id, run_id, epoch = _running_with_lease(db)
    with workspace_connection(db, WS) as conn:
        attempt = str(
            conn.execute(
                "SELECT attempt_id FROM desktop_lease WHERE id = %s", (lease_id,)
            ).fetchone()["attempt_id"]
        )
        first = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=attempt,
            epoch=epoch,
            action_sequence=1,
            action="NEXT",
            origin=ORIGIN,
        )
        runners.mark_action_dispatched(conn, action_id=first)
        _, _, revision = _state(db, run_id)
        runners.terminalize_ambiguous_attempt(
            conn,
            run_id=run_id,
            action_id=first,
            reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
            expected_revision=revision,
        )

    with workspace_connection(db, WS) as conn:
        second = runners.record_action_intent(
            conn,
            workspace_id=WS,
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=attempt,
            epoch=epoch,
            action_sequence=2,
            action="PREVIOUS",
            origin=ORIGIN,
        )
        _, _, revision = _state(db, run_id)
        with pytest.raises(runs.TerminalRun):
            runners.terminalize_ambiguous_attempt(
                conn,
                run_id=run_id,
                action_id=second,
                reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED,
                expected_revision=revision,
            )
