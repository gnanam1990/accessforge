"""Outcome precedence and status/outcome admissibility.

Covers the full cross-product rather than sampled cases, because the interesting failures here are
in the combinations nobody thought to write down by hand.

Requirements: FR-007. Invariants: INV-02, INV-03, INV-06.
"""

from __future__ import annotations

import itertools

import pytest

from accessforge_domain.outcome import (
    EvaluationInput,
    EvidenceValidity,
    evaluate,
)
from accessforge_domain.states import (
    NONTERMINAL_STATUSES,
    Condition,
    Outcome,
    RunStatus,
    admissible_outcomes,
    is_admissible_pair,
    is_terminal,
)

SOUND = EvidenceValidity(
    identity_bound=True,
    preflight_passed=True,
    evidence_chain_intact=True,
    producer_tails_closed=True,
)


def _evaluate(
    assertions: tuple[Condition, ...],
    completion: Condition,
    validity: EvidenceValidity = SOUND,
) -> Outcome:
    return evaluate(
        EvaluationInput(validity=validity, required_assertions=assertions, completion=completion)
    ).outcome


# --- full cross-product of assertion and completion states ---------------------------------


@pytest.mark.parametrize(
    ("a1", "a2", "completion"),
    list(itertools.product(Condition, Condition, Condition)),
)
def test_precedence_holds_across_the_entire_cross_product(
    a1: Condition, a2: Condition, completion: Condition
) -> None:
    conditions = (a1, a2, completion)
    outcome = _evaluate((a1, a2), completion)

    if Condition.UNKNOWN in conditions:
        # UNKNOWN outranks a simultaneous FALSE, always.
        assert outcome is Outcome.INCONCLUSIVE
    elif Condition.FALSE in conditions:
        assert outcome is Outcome.FAIL
    else:
        assert outcome is Outcome.PASS


def test_unknown_outranks_a_simultaneous_false() -> None:
    """The specific combination the precedence exists to disambiguate.

    One assertion definitively failed and another could not be observed. Reporting FAIL would
    assert a confirmed defect on incomplete evidence.
    """
    assert _evaluate((Condition.FALSE, Condition.UNKNOWN), Condition.TRUE) is Outcome.INCONCLUSIVE


def test_a_single_unknown_completion_prevents_pass() -> None:
    assert _evaluate((Condition.TRUE, Condition.TRUE), Condition.UNKNOWN) is Outcome.INCONCLUSIVE


def test_a_false_completion_is_a_fail_even_when_every_assertion_is_true() -> None:
    # Required completion is a frozen required condition, not an optional extra.
    assert _evaluate((Condition.TRUE, Condition.TRUE), Condition.FALSE) is Outcome.FAIL


def test_all_true_is_the_only_route_to_pass() -> None:
    # Allowed-path control: an always-INCONCLUSIVE evaluator must fail this.
    assert _evaluate((Condition.TRUE, Condition.TRUE), Condition.TRUE) is Outcome.PASS
    assert _evaluate((), Condition.TRUE) is Outcome.PASS


# --- unsound foundations dominate everything -----------------------------------------------


@pytest.mark.parametrize(
    "flaw",
    [
        {"identity_bound": False},
        {"preflight_passed": False},
        {"evidence_chain_intact": False},
        {"producer_tails_closed": False},
    ],
)
def test_any_unsound_foundation_forces_inconclusive_even_with_all_true_conditions(
    flaw: dict[str, bool],
) -> None:
    validity = EvidenceValidity(
        **{
            **{
                "identity_bound": True,
                "preflight_passed": True,
                "evidence_chain_intact": True,
                "producer_tails_closed": True,
            },
            **flaw,
        }
    )
    assert (
        _evaluate((Condition.TRUE, Condition.TRUE), Condition.TRUE, validity)
        is Outcome.INCONCLUSIVE
    )


def test_evidence_validity_defaults_to_unsound() -> None:
    """A caller that forgets to populate validity must not get a PASS by omission."""
    assert not EvidenceValidity().sound
    assert _evaluate((Condition.TRUE,), Condition.TRUE, EvidenceValidity()) is Outcome.INCONCLUSIVE


def test_inconclusive_always_explains_itself() -> None:
    result = evaluate(
        EvaluationInput(
            validity=EvidenceValidity(identity_bound=True),
            required_assertions=(Condition.TRUE,),
            completion=Condition.TRUE,
        )
    )
    assert result.outcome is Outcome.INCONCLUSIVE
    assert result.reasons, "an INCONCLUSIVE verdict must say what was missing"


def test_a_missing_producer_tail_is_not_hidden_by_an_intact_chain() -> None:
    """INV-06: a contiguous chain does not establish that every producer finished."""
    validity = EvidenceValidity(
        identity_bound=True,
        preflight_passed=True,
        evidence_chain_intact=True,
        producer_tails_closed=False,
    )
    result = evaluate(
        EvaluationInput(
            validity=validity,
            required_assertions=(Condition.TRUE,),
            completion=Condition.TRUE,
        )
    )
    assert result.outcome is Outcome.INCONCLUSIVE
    assert any("watermark" in r for r in result.reasons)


# --- status / outcome admissibility ---------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome", "execution_began"),
    list(itertools.product(RunStatus, Outcome, [False, True])),
)
def test_status_outcome_admissibility_cross_product(
    status: RunStatus, outcome: Outcome, execution_began: bool
) -> None:
    admissible = is_admissible_pair(status, outcome, execution_began=execution_began)

    if status in NONTERMINAL_STATUSES:
        expected = outcome is Outcome.NOT_EVALUATED
    elif status is RunStatus.COMPLETED:
        expected = outcome in {Outcome.PASS, Outcome.FAIL, Outcome.INCONCLUSIVE}
    elif status is RunStatus.INTERRUPTED:
        expected = outcome is Outcome.INCONCLUSIVE
    else:  # CANCELLED
        expected = outcome is (Outcome.INCONCLUSIVE if execution_began else Outcome.NOT_EVALUATED)

    assert admissible is expected, f"{status}/{outcome} execution_began={execution_began}"


@pytest.mark.parametrize("status", [RunStatus.INTERRUPTED, RunStatus.CANCELLED])
def test_pass_and_fail_never_attach_to_interrupted_or_cancelled_runs(
    status: RunStatus,
) -> None:
    for execution_began in (False, True):
        allowed = admissible_outcomes(status, execution_began=execution_began)
        assert Outcome.PASS not in allowed
        assert Outcome.FAIL not in allowed


def test_completed_runs_are_never_not_evaluated() -> None:
    assert Outcome.NOT_EVALUATED not in admissible_outcomes(RunStatus.COMPLETED)


def test_terminal_classification() -> None:
    assert is_terminal(RunStatus.COMPLETED)
    assert is_terminal(RunStatus.INTERRUPTED)
    assert is_terminal(RunStatus.CANCELLED)
    for status in NONTERMINAL_STATUSES:
        assert not is_terminal(status)


def test_the_state_vocabulary_has_not_grown() -> None:
    """Adding a state is a contract change, not an implementation detail."""
    assert {s.value for s in RunStatus} == {
        "QUEUED",
        "LEASED",
        "RUNNING",
        "FINALIZING",
        "COMPLETED",
        "INTERRUPTED",
        "CANCELLED",
    }
    assert {o.value for o in Outcome} == {"NOT_EVALUATED", "PASS", "FAIL", "INCONCLUSIVE"}
    assert {c.value for c in Condition} == {"TRUE", "FALSE", "UNKNOWN"}
