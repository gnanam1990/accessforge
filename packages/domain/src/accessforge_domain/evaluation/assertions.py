"""Evaluating frozen assertions against admitted evidence.

Every assertion gets a tri-state value and a reference to the evidence that produced it. The
reference is not decoration: an INCONCLUSIVE verdict that cannot say which observation was missing
is
indistinguishable from a broken evaluator, and a FAIL that cannot point at the announcement it read
is not reviewable.

Two rules here are the ones an implementation drifts away from under pressure.

**An evaluator-derived assertion is never observer-authored.** CONTRACTS section 7 permits the
evaluator to derive assertions from retained reader evidence, and forbids "pretending they came from
the independent application observer". So the provenance travels with the value, and
:func:`assert_provenance_permitted` refuses a reader-derived value for an assertion whose observer
is
the application. The two are not interchangeable: the reader tells you what was announced, and the
application tells you what actually happened in the backend. A page can announce "Request submitted"
having submitted nothing.

**A missing observation is UNKNOWN, never FALSE.** The temptation runs the other way -- "the
required announcement was not observed" reads like "the required announcement did not happen",
and that reading turns every capture failure into a reported accessibility defect (INV-02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from accessforge_domain.journeys.assertions import (
    ASSERTION_OBSERVERS,
    Assertion,
    AssertionKind,
    Observer,
)
from accessforge_domain.states import Condition


class Provenance(StrEnum):
    """Where an assertion's value came from.

    ``EVALUATOR_DERIVED`` is a first-class value rather than an absence, because the interesting
    question about a derived assertion is not whether it is allowed -- it often is -- but whether it
    is being presented as something it is not.
    """

    OBSERVER_AUTHORED = "OBSERVER_AUTHORED"
    """Submitted by the identity the assertion's observer names, as an authenticated record."""

    EVALUATOR_DERIVED = "EVALUATOR_DERIVED"
    """Computed by the evaluator from retained evidence. Permitted, and never disguised."""

    ABSENT = "ABSENT"
    """No observation exists. The value must be UNKNOWN."""


class ProvenanceError(Exception):
    """An assertion value claims an authority its source does not have."""


@dataclass(frozen=True, slots=True)
class AssertionOutcome:
    """One frozen assertion's evaluated value, with where it came from and what it rests on."""

    assertion_id: str
    kind: AssertionKind
    condition: Condition
    provenance: Provenance
    #: Canonical event ids, artifact digests, or the unknown reason. Empty only for ABSENT.
    evidence_refs: tuple[str, ...] = ()
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        if self.provenance is Provenance.ABSENT and self.condition is not Condition.UNKNOWN:
            raise ProvenanceError(
                f"assertion {self.assertion_id} has no observation but a value of "
                f"{self.condition}. An absent observation is UNKNOWN: reading it as FALSE turns a "
                "capture failure into a reported accessibility defect (INV-02)."
            )
        if self.condition is not Condition.UNKNOWN and not self.evidence_refs:
            raise ProvenanceError(
                f"assertion {self.assertion_id} is {self.condition} with no evidence reference. A "
                "definite value nobody can trace to an observation is not reviewable."
            )
        if self.condition is Condition.UNKNOWN and self.unknown_reason is None:
            raise ProvenanceError(
                f"assertion {self.assertion_id} is UNKNOWN without a reason. 'We do not know' is "
                "only useful to a reader who is told what was not known."
            )


#: Which provenances may supply a value for an assertion decided by each observer.
#:
#: The READER row is the permissive one and it is deliberate: the evaluator is allowed to derive a
#: reader assertion from a retained transcript, because the transcript *is* the reader's evidence.
#: The APPLICATION_OBSERVER row is the strict one for the same reason in reverse -- there is no
#: retained artifact from which "the backend received exactly one request" can be derived, so a
#: derived value there would be the evaluator inventing a backend observation.
_PERMITTED_PROVENANCE: dict[Observer, frozenset[Provenance]] = {
    Observer.READER: frozenset({Provenance.OBSERVER_AUTHORED, Provenance.EVALUATOR_DERIVED}),
    Observer.APPLICATION_OBSERVER: frozenset({Provenance.OBSERVER_AUTHORED}),
    Observer.EFFECT_MONITOR: frozenset({Provenance.OBSERVER_AUTHORED}),
    Observer.FUNCTIONAL_TEST: frozenset({Provenance.OBSERVER_AUTHORED}),
}


def assert_provenance_permitted(outcome: AssertionOutcome) -> None:
    """Refuse a value whose source is not entitled to decide this assertion.

    ABSENT is always permitted: not observing something is available to everyone, and refusing it
    would leave a caller with no way to report a missing observation at all.
    """
    if outcome.provenance is Provenance.ABSENT:
        return

    observer = ASSERTION_OBSERVERS[outcome.kind]
    permitted = _PERMITTED_PROVENANCE[observer]
    if outcome.provenance not in permitted:
        raise ProvenanceError(
            f"assertion {outcome.assertion_id} is decided by {observer} and this value is "
            f"{outcome.provenance}. The evaluator may derive a reader assertion from a retained "
            "transcript, because the transcript is the reader's own evidence. It may not derive an "
            "application observation: there is no retained artifact from which 'the backend "
            "received exactly one request' follows, so a derived value there is the evaluator "
            "inventing a backend observation and presenting it as one (CONTRACTS section 7)."
        )


@dataclass(frozen=True, slots=True)
class AssertionEvaluation:
    outcomes: tuple[AssertionOutcome, ...] = field(default_factory=tuple)

    def conditions(self) -> tuple[Condition, ...]:
        return tuple(o.condition for o in self.outcomes)

    def unknown_reasons(self) -> tuple[str, ...]:
        return tuple(
            f"{o.assertion_id}: {o.unknown_reason}"
            for o in self.outcomes
            if o.condition is Condition.UNKNOWN and o.unknown_reason
        )

    def false_assertions(self) -> tuple[str, ...]:
        return tuple(o.assertion_id for o in self.outcomes if o.condition is Condition.FALSE)


def evaluate_assertions(
    required: tuple[Assertion, ...], values: dict[str, AssertionOutcome]
) -> AssertionEvaluation:
    """Line the frozen assertions up against the values that were produced for them.

    An assertion with no value becomes UNKNOWN with a reason saying so, rather than being dropped.
    Dropping it would shorten the list of required conditions, and a shorter list of required
    conditions is easier to satisfy -- which is precisely the direction a repair must not be able to
    push the evaluation (INV-16).
    """
    outcomes: list[AssertionOutcome] = []
    for assertion in required:
        value = values.get(assertion.assertion_id)
        if value is None:
            outcomes.append(
                AssertionOutcome(
                    assertion_id=assertion.assertion_id,
                    kind=assertion.kind,
                    condition=Condition.UNKNOWN,
                    provenance=Provenance.ABSENT,
                    unknown_reason=(
                        "no observation was submitted for this assertion. It stays in the required "
                        "set as UNKNOWN rather than being dropped: dropping it would shorten the "
                        "list of conditions a PASS has to satisfy."
                    ),
                )
            )
            continue
        if value.kind is not assertion.kind:
            raise ProvenanceError(
                f"assertion {assertion.assertion_id} is a {assertion.kind} and the submitted value "
                f"claims to be a {value.kind}; the observer entitled to decide it differs"
            )
        assert_provenance_permitted(value)
        outcomes.append(value)
    return AssertionEvaluation(tuple(outcomes))
