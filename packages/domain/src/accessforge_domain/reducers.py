"""Pure state transitions for a run.

Every function here is total and side-effect free: given a state and an event, it returns the next
state or refuses. Nothing consults a clock, a database or a lease service — callers supply what
they observed, and the reducer decides what that permits.

The cancellation rules (CONTRACTS section 5) carry most of the subtlety, because "we asked it to
stop" and "it actually stopped" are different facts and the product must never conflate them:

* Cancellation is a two-step process. Requesting it records the request and fences new admission;
  it does not end the run.
* A queued run that was never leased and provably admitted no action can go straight to CANCELLED,
  because there is nothing that could still be executing.
* Otherwise CANCELLED requires a stop acknowledgement bound to the *current* lease epoch and no
  unresolved in-flight action. A stale-epoch acknowledgement proves the previous session stopped,
  not this one.
* A lease timeout is not stop proof. A machine that stopped answering may still be typing.
* Lost acknowledgement or an ambiguous dispatched action ends INTERRUPTED with a reason and
  quarantines the session, rather than guessing (INV-09, INV-13).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .states import (
    NORMAL_PROGRESSION,
    Outcome,
    RunStatus,
    admissible_outcomes,
    is_terminal,
)


class TransitionError(Exception):
    """A transition was refused.

    Refusal is the safe default. A reducer that guessed would let an inadmissible state into the
    journal, and terminal records are immutable (INV-11) — there is no repairing it afterwards.
    """


@dataclass(frozen=True, slots=True)
class RunState:
    """The reducible state of a run.

    ``revision`` increments on every accepted transition and is what optimistic concurrency
    checks against. ``lease_epoch`` distinguishes one physical desktop session from the next.
    """

    run_id: str
    status: RunStatus = RunStatus.QUEUED
    outcome: Outcome = Outcome.NOT_EVALUATED
    revision: int = 0
    lease_epoch: int = 0

    execution_began: bool = False
    """True once any action has been admitted. Decides which cancelled outcome applies."""

    unresolved_action: bool = False
    """True while an action has been dispatched to the OS with no recorded result. An
    ambiguous action, not a pending one, is what blocks a clean stop."""

    cancel_requested_at: str | None = None
    cancellation_revision: int | None = None
    stop_acknowledged_at: str | None = None
    stop_acknowledged_epoch: int | None = None

    ambiguity_reason: str | None = None
    quarantined: bool = False

    @property
    def cancellation_requested(self) -> bool:
        return self.cancel_requested_at is not None

    @property
    def physically_stopped(self) -> bool:
        """Whether a stop has actually been acknowledged for the *current* epoch.

        The UI must show "cancellation requested" rather than "stopped" while this is False.
        """
        return (
            self.stop_acknowledged_at is not None
            and self.stop_acknowledged_epoch == self.lease_epoch
        )


def _require_expected_revision(state: RunState, expected_revision: int | None) -> None:
    if expected_revision is not None and expected_revision != state.revision:
        raise TransitionError(
            f"stale revision: expected {expected_revision}, actual {state.revision}"
        )


def _require_nonterminal(state: RunState) -> None:
    if is_terminal(state.status):
        raise TransitionError(
            f"run {state.run_id} is terminal in {state.status}; terminal records are immutable "
            "and a retry must create a new linked run"
        )


def _advance(state: RunState, **changes: object) -> RunState:
    return replace(state, revision=state.revision + 1, **changes)  # type: ignore[arg-type]


def progress(state: RunState, *, expected_revision: int | None = None) -> RunState:
    """Advance one step along the normal lifecycle."""
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)

    if state.cancellation_requested:
        raise TransitionError("cancellation has been requested; no further admission is permitted")

    next_status = NORMAL_PROGRESSION.get(state.status)
    if next_status is None:
        raise TransitionError(f"no normal progression from {state.status}")

    if next_status is RunStatus.COMPLETED:
        raise TransitionError(
            "completion must go through complete(), which requires an evaluated outcome"
        )

    began = state.execution_began or next_status is RunStatus.RUNNING
    return _advance(state, status=next_status, execution_began=began)


def admit_action(state: RunState, *, expected_revision: int | None = None) -> RunState:
    """Record that an action has been dispatched to the operating system.

    Intent is durable before dispatch, so the unresolved flag is set here rather than after the
    result arrives. That asymmetry is deliberate: a crash between dispatch and result must leave
    evidence that something may have happened.
    """
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if state.status is not RunStatus.RUNNING:
        raise TransitionError(f"actions may only be admitted while RUNNING, not {state.status}")
    if state.cancellation_requested:
        raise TransitionError("cancellation fence is active; no new action may be admitted")
    if state.unresolved_action:
        raise TransitionError("an action is already unresolved; one at a time per session")
    return _advance(state, unresolved_action=True, execution_began=True)


def resolve_action(state: RunState, *, expected_revision: int | None = None) -> RunState:
    """Record an unambiguous result for the dispatched action."""
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if not state.unresolved_action:
        raise TransitionError("no action is currently unresolved")
    return _advance(state, unresolved_action=False)


def request_cancellation(
    state: RunState, *, requested_at: str, expected_revision: int | None = None
) -> RunState:
    """Step one of cancellation: fence new admission and record the request.

    This never ends the run on its own.
    """
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if state.cancellation_requested:
        return state  # idempotent; re-requesting changes nothing
    return _advance(state, cancel_requested_at=requested_at, cancellation_revision=state.revision)


def acknowledge_stop(
    state: RunState,
    *,
    acknowledged_at: str,
    epoch: int,
    expected_revision: int | None = None,
) -> RunState:
    """Record a runner's acknowledgement that it has physically stopped.

    An acknowledgement from a previous lease epoch is rejected: it proves the earlier session
    stopped, which says nothing about the one currently holding the desktop.
    """
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if epoch != state.lease_epoch:
        raise TransitionError(
            f"stop acknowledgement is bound to epoch {epoch}, but the current epoch is "
            f"{state.lease_epoch}; a stale acknowledgement is not stop proof"
        )
    return _advance(state, stop_acknowledged_at=acknowledged_at, stop_acknowledged_epoch=epoch)


def cancel(state: RunState, *, expected_revision: int | None = None) -> RunState:
    """Step two: finalize as CANCELLED, if that is actually established."""
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if not state.cancellation_requested:
        raise TransitionError("cancellation must be requested before a run can be cancelled")

    # Two independent questions, previously conflated:
    #
    #   (a) Is a stop acknowledgement required?  Yes once a runner holds the desktop, i.e. from
    #       LEASED onward, because something out there might still be acting.
    #   (b) Which outcome applies?  NOT_EVALUATED if execution never began, INCONCLUSIVE if it
    #       did — this keys on execution, not on status.
    #
    # Keying (b) on `status is QUEUED` produced CANCELLED/INCONCLUSIVE for a LEASED run that had
    # admitted nothing, which is inadmissible under CONTRACTS section 5.
    provably_never_dispatched = (
        state.status is RunStatus.QUEUED
        and not state.execution_began
        and not state.unresolved_action
    )

    if not provably_never_dispatched:
        if state.unresolved_action:
            raise TransitionError(
                "an action is still unresolved; an ambiguous dispatched action ends INTERRUPTED, "
                "not CANCELLED"
            )
        if not state.physically_stopped:
            raise TransitionError(
                "no stop acknowledgement for the current epoch; the run is "
                "cancellation-requested, not physically stopped"
            )

    outcome = Outcome.INCONCLUSIVE if state.execution_began else Outcome.NOT_EVALUATED
    return _advance(state, status=RunStatus.CANCELLED, outcome=outcome)


def interrupt(state: RunState, *, reason: str, expected_revision: int | None = None) -> RunState:
    """End the run INTERRUPTED and quarantine the session.

    Used for lost acknowledgement, ambiguous dispatched actions, lease expiry and infrastructure
    failure. An interrupted run is always INCONCLUSIVE: infrastructure failure is not a
    reproduced accessibility defect.
    """
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if not reason.strip():
        raise TransitionError("an interruption must record why it was ambiguous")
    return _advance(
        state,
        status=RunStatus.INTERRUPTED,
        outcome=Outcome.INCONCLUSIVE,
        ambiguity_reason=reason,
        quarantined=True,
    )


def complete(
    state: RunState, *, outcome: Outcome, expected_revision: int | None = None
) -> RunState:
    """Finalize a run with an evaluated outcome.

    The outcome must come from ``outcome.evaluate``; this reducer only checks admissibility. It
    will not accept NOT_EVALUATED, and it will not complete a run with an unresolved action.
    """
    _require_expected_revision(state, expected_revision)
    _require_nonterminal(state)
    if state.status is not RunStatus.FINALIZING:
        raise TransitionError(f"completion requires FINALIZING, not {state.status}")
    if state.unresolved_action:
        raise TransitionError(
            "an action is still unresolved; the run cannot be reported as completed"
        )
    if outcome not in admissible_outcomes(RunStatus.COMPLETED):
        raise TransitionError(f"{outcome} is not admissible for a completed run")
    return _advance(state, status=RunStatus.COMPLETED, outcome=outcome)
