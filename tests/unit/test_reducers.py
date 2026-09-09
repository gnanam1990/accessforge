"""Run transition reducers, with emphasis on the cancellation rules.

Requirements: FR-007, FR-015. Invariants: INV-09, INV-11, INV-13.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from accessforge_domain.reducers import (
    RunState,
    TransitionError,
    acknowledge_stop,
    admit_action,
    cancel,
    complete,
    interrupt,
    progress,
    request_cancellation,
    resolve_action,
)
from accessforge_domain.states import Outcome, RunStatus


def running(**overrides: object) -> RunState:
    state = RunState(run_id="r1")
    state = progress(state)  # LEASED
    state = progress(state)  # RUNNING
    # RunState uses slots, so replace() rather than __dict__ manipulation.
    return replace(state, **overrides) if overrides else state


# --- normal lifecycle -----------------------------------------------------------------------


def test_normal_progression_reaches_finalizing_and_then_completes() -> None:
    state = RunState(run_id="r1")
    assert state.status is RunStatus.QUEUED
    for expected in (RunStatus.LEASED, RunStatus.RUNNING, RunStatus.FINALIZING):
        state = progress(state)
        assert state.status is expected
    state = complete(state, outcome=Outcome.PASS)
    assert state.status is RunStatus.COMPLETED
    assert state.outcome is Outcome.PASS


def test_every_accepted_transition_increments_the_revision() -> None:
    state = RunState(run_id="r1")
    revisions = []
    for _ in range(3):
        state = progress(state)
        revisions.append(state.revision)
    assert revisions == [1, 2, 3]


def test_a_stale_expected_revision_is_refused() -> None:
    state = progress(RunState(run_id="r1"))
    with pytest.raises(TransitionError, match="stale revision"):
        progress(state, expected_revision=0)


def test_terminal_records_are_immutable() -> None:
    """INV-11: a retry creates a new linked run; it never resumes a terminal one."""
    state = complete(progress(progress(progress(RunState(run_id="r1")))), outcome=Outcome.FAIL)
    for attempt in (
        lambda: progress(state),
        lambda: admit_action(state),
        lambda: cancel(state),
        lambda: interrupt(state, reason="x"),
        lambda: complete(state, outcome=Outcome.PASS),
    ):
        with pytest.raises(TransitionError, match="terminal"):
            attempt()


def test_completion_requires_finalizing_and_an_evaluated_outcome() -> None:
    with pytest.raises(TransitionError, match="FINALIZING"):
        complete(running(), outcome=Outcome.PASS)
    finalizing = progress(running())
    with pytest.raises(TransitionError, match="not admissible"):
        complete(finalizing, outcome=Outcome.NOT_EVALUATED)


def test_completion_is_refused_while_an_action_is_unresolved() -> None:
    state = admit_action(running())
    # Reach FINALIZING without resolving: the reducer must still refuse to complete.
    state = replace(state, status=RunStatus.FINALIZING)
    with pytest.raises(TransitionError, match="unresolved"):
        complete(state, outcome=Outcome.PASS)


# --- cancellation ---------------------------------------------------------------------------


def test_requesting_cancellation_does_not_end_the_run() -> None:
    state = request_cancellation(running(), requested_at="2026-09-09T12:00:00Z")
    assert state.status is RunStatus.RUNNING
    assert state.cancellation_requested
    assert not state.physically_stopped, "requested is not stopped"


def test_requesting_cancellation_is_idempotent() -> None:
    first = request_cancellation(running(), requested_at="2026-09-09T12:00:00Z")
    second = request_cancellation(first, requested_at="2026-09-09T12:05:00Z")
    assert second is first


def test_cancellation_fences_new_admission() -> None:
    """INV-13: no new action may be admitted once cancellation is requested."""
    state = request_cancellation(running(), requested_at="2026-09-09T12:00:00Z")
    with pytest.raises(TransitionError, match="fence"):
        admit_action(state)
    with pytest.raises(TransitionError, match="no further admission"):
        progress(state)


def test_a_never_admitted_queued_run_cancels_immediately() -> None:
    state = request_cancellation(RunState(run_id="r1"), requested_at="2026-09-09T12:00:00Z")
    cancelled = cancel(state)
    assert cancelled.status is RunStatus.CANCELLED
    # Nothing was ever evaluated, so NOT_EVALUATED rather than INCONCLUSIVE.
    assert cancelled.outcome is Outcome.NOT_EVALUATED


def test_a_started_run_cannot_cancel_without_a_stop_acknowledgement() -> None:
    state = request_cancellation(running(), requested_at="2026-09-09T12:00:00Z")
    with pytest.raises(TransitionError, match="not physically stopped"):
        cancel(state)


def test_a_started_run_cancels_once_the_stop_is_acknowledged() -> None:
    state = request_cancellation(running(), requested_at="2026-09-09T12:00:00Z")
    state = acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=0)
    assert state.physically_stopped
    cancelled = cancel(state)
    assert cancelled.status is RunStatus.CANCELLED
    # Execution began, so the run evaluated something incompletely.
    assert cancelled.outcome is Outcome.INCONCLUSIVE


def test_a_stale_epoch_acknowledgement_is_not_stop_proof() -> None:
    """A previous session stopping says nothing about the one holding the desktop now."""
    state = running()
    state = replace(state, lease_epoch=2)
    state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
    with pytest.raises(TransitionError, match="stale acknowledgement"):
        acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=1)


def test_an_unresolved_action_ends_interrupted_not_cancelled() -> None:
    """INV-09: an ambiguous dispatched action is never resolved by assumption."""
    state = admit_action(running())
    state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
    state = acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=0)
    with pytest.raises(TransitionError, match="unresolved"):
        cancel(state)

    interrupted = interrupt(state, reason="stop acknowledged with an action still in flight")
    assert interrupted.status is RunStatus.INTERRUPTED
    assert interrupted.outcome is Outcome.INCONCLUSIVE
    assert interrupted.quarantined


def test_cancelling_without_requesting_first_is_refused() -> None:
    with pytest.raises(TransitionError, match="must be requested"):
        cancel(running())


def test_resolving_an_action_clears_the_ambiguity() -> None:
    state = admit_action(running())
    assert state.unresolved_action
    state = resolve_action(state)
    assert not state.unresolved_action
    state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
    state = acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=0)
    assert cancel(state).status is RunStatus.CANCELLED


def test_only_one_action_may_be_in_flight_at_a_time() -> None:
    """INV-10, in reducer form: one physical desktop, one admitted action."""
    state = admit_action(running())
    with pytest.raises(TransitionError, match="already unresolved"):
        admit_action(state)


# --- interruption ---------------------------------------------------------------------------


def test_interruption_requires_a_reason() -> None:
    with pytest.raises(TransitionError, match="record why"):
        interrupt(running(), reason="   ")


def test_interruption_is_always_inconclusive_and_quarantines() -> None:
    """Infrastructure failure is not a reproduced accessibility defect."""
    state = interrupt(running(), reason="lease expired without acknowledgement")
    assert state.outcome is Outcome.INCONCLUSIVE
    assert state.quarantined
    assert state.ambiguity_reason == "lease expired without acknowledgement"


def test_a_leased_run_that_admitted_nothing_cancels_as_not_evaluated() -> None:
    """Regression: found by property testing, not by hand.

    An earlier implementation decided the cancelled outcome from the status (`is QUEUED`) rather
    than from whether execution actually began. A run that reached LEASED but admitted no action
    was therefore finalized CANCELLED/INCONCLUSIVE, which CONTRACTS section 5 forbids: before any
    admitted execution the outcome is NOT_EVALUATED.

    The acknowledgement requirement and the outcome are separate questions. A leased runner holds
    the desktop, so a stop acknowledgement is still required here — but nothing was evaluated.
    """
    state = progress(RunState(run_id="r1"))  # QUEUED -> LEASED
    assert state.status is RunStatus.LEASED
    assert not state.execution_began

    state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")

    # Still requires acknowledgement, because a runner already holds the desktop.
    with pytest.raises(TransitionError, match="not physically stopped"):
        cancel(state)

    state = acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=0)
    cancelled = cancel(state)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.outcome is Outcome.NOT_EVALUATED


def test_a_running_run_has_begun_execution_even_before_its_first_action() -> None:
    """Entering RUNNING means the runner is driving the journey, so cancellation from there is
    INCONCLUSIVE rather than NOT_EVALUATED."""
    state = running()
    assert state.execution_began
    state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
    state = acknowledge_stop(state, acknowledged_at="2026-09-09T12:00:05Z", epoch=0)
    assert cancel(state).outcome is Outcome.INCONCLUSIVE
