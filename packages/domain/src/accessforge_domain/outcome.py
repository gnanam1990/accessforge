"""Deterministic outcome evaluation.

This is the single place a PASS can be produced, and it is a pure function of recorded evidence.
No model, no clock, no database, no confidence score. Anyone holding the evidence can recompute
the verdict and get the same answer.

The precedence in CONTRACTS section 5 is exact, and the ordering is the whole point:

  1. invalid or incomplete identity, preflight or evidence      -> INCONCLUSIVE
  2. any UNKNOWN required assertion or completion observation   -> INCONCLUSIVE
  3. any FALSE required assertion or completion observation     -> FAIL
  4. all required conditions TRUE                               -> PASS

UNKNOWN outranks a simultaneous FALSE. A run that observed one genuine failure *and* failed to
observe something else is INCONCLUSIVE, not FAIL: the unobserved condition might have changed the
interpretation of the failure, and reporting a confirmed defect on incomplete evidence is exactly
the harm INV-02 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .states import Condition, Outcome


@dataclass(frozen=True, slots=True)
class EvidenceValidity:
    """Whether the run's foundations are sound enough for its observations to mean anything.

    Every field defaults to the *unsound* value. A caller that forgets to populate one gets
    INCONCLUSIVE rather than an accidental PASS.
    """

    identity_bound: bool = False
    """Manifest identity binds exact journey, assertions, source, build, fixture and runner
    versions (INV-03)."""

    preflight_passed: bool = False
    """The reader and environment preflight actually succeeded for this attempt."""

    evidence_chain_intact: bool = False
    """No gaps, forks or missing required artifacts in the admitted event chain (INV-06)."""

    producer_tails_closed: bool = False
    """Every required producer submitted an authenticated closing watermark. A contiguous chain
    alone does not establish this."""

    def reasons(self) -> tuple[str, ...]:
        problems = []
        if not self.identity_bound:
            problems.append("identity not bound to exact versions")
        if not self.preflight_passed:
            problems.append("preflight did not pass")
        if not self.evidence_chain_intact:
            problems.append("evidence chain has gaps, forks or missing artifacts")
        if not self.producer_tails_closed:
            problems.append("required producer closing watermarks missing")
        return tuple(problems)

    @property
    def sound(self) -> bool:
        return not self.reasons()


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    """Everything needed to decide an outcome, and nothing else."""

    validity: EvidenceValidity
    required_assertions: tuple[Condition, ...] = ()
    completion: Condition = Condition.UNKNOWN
    """The frozen task completion observation.

    Required, not an optional extra, and defaulting to UNKNOWN. A run that never established
    whether the task completed cannot PASS.
    """


@dataclass(frozen=True, slots=True)
class Evaluation:
    outcome: Outcome
    reasons: tuple[str, ...] = field(default_factory=tuple)


def evaluate(evaluation_input: EvaluationInput) -> Evaluation:
    """Apply the precedence rules and explain the result.

    The reasons are part of the contract, not decoration: an INCONCLUSIVE verdict that cannot say
    what was missing is indistinguishable from a broken evaluator.
    """
    validity = evaluation_input.validity

    # 1. Foundations first. Observations made on unsound footing are not evidence of anything.
    if not validity.sound:
        return Evaluation(Outcome.INCONCLUSIVE, validity.reasons())

    conditions = (*evaluation_input.required_assertions, evaluation_input.completion)

    # 2. UNKNOWN outranks FALSE. Checked before FALSE, deliberately.
    unknown_count = sum(1 for c in conditions if c is Condition.UNKNOWN)
    if unknown_count:
        detail = (
            ["required completion observation is unknown"]
            if (evaluation_input.completion is Condition.UNKNOWN)
            else []
        )
        unknown_assertions = sum(
            1 for c in evaluation_input.required_assertions if c is Condition.UNKNOWN
        )
        if unknown_assertions:
            detail.append(f"{unknown_assertions} required assertion(s) unknown")
        return Evaluation(Outcome.INCONCLUSIVE, tuple(detail))

    # 3. Now every condition is definitively TRUE or FALSE.
    false_assertions = sum(1 for c in evaluation_input.required_assertions if c is Condition.FALSE)
    completion_false = evaluation_input.completion is Condition.FALSE
    if false_assertions or completion_false:
        detail = []
        if false_assertions:
            detail.append(f"{false_assertions} required assertion(s) false")
        if completion_false:
            detail.append("required completion observation is false")
        return Evaluation(Outcome.FAIL, tuple(detail))

    # 4. Everything required is TRUE.
    return Evaluation(Outcome.PASS)
