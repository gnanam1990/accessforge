"""Property tests: PASS must be unreachable without complete evidence.

The unit tests check the cases we designed. These check the cases a generator invents, over
thousands of random event sequences and evidence combinations. The property is the product's
central safety claim, so it is worth stating as a property rather than a list of examples.

Invariants: INV-02, INV-03, INV-06, INV-11, INV-13.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from accessforge_domain.outcome import EvaluationInput, EvidenceValidity, evaluate
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
from accessforge_domain.states import (
    Condition,
    Outcome,
    RunStatus,
    is_admissible_pair,
    is_terminal,
)

conditions = st.sampled_from(list(Condition))
validity = st.builds(
    EvidenceValidity,
    identity_bound=st.booleans(),
    preflight_passed=st.booleans(),
    evidence_chain_intact=st.booleans(),
    producer_tails_closed=st.booleans(),
)


@given(
    v=validity,
    assertions=st.lists(conditions, max_size=6),
    completion=conditions,
)
def test_pass_requires_sound_evidence_and_every_condition_true(
    v: EvidenceValidity, assertions: list[Condition], completion: Condition
) -> None:
    result = evaluate(
        EvaluationInput(validity=v, required_assertions=tuple(assertions), completion=completion)
    )
    if result.outcome is Outcome.PASS:
        # The only way to PASS, stated as a property rather than an example.
        assert v.sound
        assert completion is Condition.TRUE
        assert all(c is Condition.TRUE for c in assertions)


@given(
    assertions=st.lists(conditions, max_size=6),
    completion=conditions,
)
def test_any_unknown_forces_inconclusive_regardless_of_anything_else(
    assertions: list[Condition], completion: Condition
) -> None:
    # Sound validity is constructed rather than filtered: assume() discarded 15 of every 16
    # generated cases, which Hypothesis rightly flags as distorting the distribution.
    v = EvidenceValidity(True, True, True, True)
    result = evaluate(
        EvaluationInput(validity=v, required_assertions=tuple(assertions), completion=completion)
    )
    if Condition.UNKNOWN in (*assertions, completion):
        assert result.outcome is Outcome.INCONCLUSIVE


@given(
    assertions=st.lists(conditions, min_size=1, max_size=6),
    completion=conditions,
)
def test_evaluation_is_deterministic(assertions: list[Condition], completion: Condition) -> None:
    """Two evaluations of identical evidence must agree, always."""
    payload = EvaluationInput(
        validity=EvidenceValidity(True, True, True, True),
        required_assertions=tuple(assertions),
        completion=completion,
    )
    assert evaluate(payload) == evaluate(payload)


# --- random event sequences against the reducers --------------------------------------------

STEPS = [
    "progress",
    "admit",
    "resolve",
    "request_cancel",
    "acknowledge",
    "cancel",
    "interrupt",
    "complete_pass",
]


@settings(max_examples=400, suppress_health_check=[HealthCheck.filter_too_much])
@given(steps=st.lists(st.sampled_from(STEPS), min_size=1, max_size=14))
def test_no_event_sequence_produces_an_inadmissible_state(steps: list[str]) -> None:
    """Whatever order events arrive in, the reducers never admit an illegal state.

    Refused transitions are expected and ignored; what matters is that no accepted sequence
    leaves the run in a status/outcome pair the contract forbids.
    """
    state = RunState(run_id="r1")

    for step in steps:
        try:
            if step == "progress":
                state = progress(state)
            elif step == "admit":
                state = admit_action(state)
            elif step == "resolve":
                state = resolve_action(state)
            elif step == "request_cancel":
                state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
            elif step == "acknowledge":
                state = acknowledge_stop(
                    state, acknowledged_at="2026-09-09T12:00:05Z", epoch=state.lease_epoch
                )
            elif step == "cancel":
                state = cancel(state)
            elif step == "interrupt":
                state = interrupt(state, reason="generated sequence")
            elif step == "complete_pass":
                state = complete(state, outcome=Outcome.PASS)
        except TransitionError:
            continue  # refusal is a valid response, not a failure

        assert is_admissible_pair(
            state.status, state.outcome, execution_began=state.execution_began
        ), f"inadmissible pair reached: {state.status}/{state.outcome} via {steps}"


@settings(max_examples=400, suppress_health_check=[HealthCheck.filter_too_much])
@given(steps=st.lists(st.sampled_from(STEPS), min_size=1, max_size=14))
def test_a_run_that_reaches_pass_went_through_finalizing(steps: list[str]) -> None:
    """PASS is reachable only by completing from FINALIZING, never by cancellation or interrupt."""
    state = RunState(run_id="r1")
    saw_finalizing = False

    for step in steps:
        try:
            if step == "progress":
                state = progress(state)
            elif step == "admit":
                state = admit_action(state)
            elif step == "resolve":
                state = resolve_action(state)
            elif step == "request_cancel":
                state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
            elif step == "acknowledge":
                state = acknowledge_stop(
                    state, acknowledged_at="2026-09-09T12:00:05Z", epoch=state.lease_epoch
                )
            elif step == "cancel":
                state = cancel(state)
            elif step == "interrupt":
                state = interrupt(state, reason="generated sequence")
            elif step == "complete_pass":
                state = complete(state, outcome=Outcome.PASS)
        except TransitionError:
            continue
        if state.status is RunStatus.FINALIZING:
            saw_finalizing = True

    if state.outcome is Outcome.PASS:
        assert state.status is RunStatus.COMPLETED
        assert saw_finalizing


@settings(max_examples=300)
@given(steps=st.lists(st.sampled_from(STEPS), min_size=1, max_size=14))
def test_terminal_states_are_absorbing(steps: list[str]) -> None:
    """INV-11: once terminal, no further transition is ever accepted."""
    state = RunState(run_id="r1")
    terminal_at: RunState | None = None

    for step in steps:
        before = state
        try:
            if step == "progress":
                state = progress(state)
            elif step == "admit":
                state = admit_action(state)
            elif step == "resolve":
                state = resolve_action(state)
            elif step == "request_cancel":
                state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
            elif step == "acknowledge":
                state = acknowledge_stop(
                    state, acknowledged_at="2026-09-09T12:00:05Z", epoch=state.lease_epoch
                )
            elif step == "cancel":
                state = cancel(state)
            elif step == "interrupt":
                state = interrupt(state, reason="generated sequence")
            elif step == "complete_pass":
                state = complete(state, outcome=Outcome.PASS)
        except TransitionError:
            state = before
            continue

        if terminal_at is not None:
            raise AssertionError(f"transition accepted after terminal state {terminal_at.status}")
        if is_terminal(state.status):
            terminal_at = state


@settings(max_examples=300)
@given(steps=st.lists(st.sampled_from(STEPS), min_size=1, max_size=14))
def test_cancelled_after_execution_is_never_not_evaluated(steps: list[str]) -> None:
    """A run that actually did something cannot claim it evaluated nothing."""
    state = RunState(run_id="r1")
    for step in steps:
        try:
            if step == "progress":
                state = progress(state)
            elif step == "admit":
                state = admit_action(state)
            elif step == "resolve":
                state = resolve_action(state)
            elif step == "request_cancel":
                state = request_cancellation(state, requested_at="2026-09-09T12:00:00Z")
            elif step == "acknowledge":
                state = acknowledge_stop(
                    state, acknowledged_at="2026-09-09T12:00:05Z", epoch=state.lease_epoch
                )
            elif step == "cancel":
                state = cancel(state)
            elif step == "interrupt":
                state = interrupt(state, reason="generated sequence")
            elif step == "complete_pass":
                state = complete(state, outcome=Outcome.PASS)
        except TransitionError:
            continue

    if state.status is RunStatus.CANCELLED and state.execution_began:
        assert state.outcome is Outcome.INCONCLUSIVE
