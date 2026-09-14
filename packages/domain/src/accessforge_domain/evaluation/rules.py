"""Evaluate explicitly frozen rules, never descriptions or caller-selected success criteria.

Reader values are evaluator-derived; effect values are produced on the independent observer side.
The callers must authenticate and bind retained source records before constructing these inputs.
This module does not turn unauthenticated JSON into evidence or decide a run outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from accessforge_domain.functional_validation import ValidationObservation
from accessforge_domain.journeys.assertions import Assertion, AssertionKind, AssertionSet
from accessforge_domain.states import Condition

from .assertions import AssertionOutcome, Provenance


@dataclass(frozen=True, slots=True)
class ReaderSample:
    action_sequence: int
    event_id: str
    phrase: str | None
    capture_unknown: bool = False
    redacted: bool = False
    # Supplied only by the trusted retained-action join, never reader/navigator JSON.
    next_action_verified: bool = False
    canonical_sequence: int | None = None


def derive_reader_assertion(
    assertion: Assertion, samples: tuple[ReaderSample, ...]
) -> AssertionOutcome:
    rule = assertion.evaluation_rule
    reason = "no supported frozen reader predicate; descriptions are not executable expectations"
    refs: tuple[str, ...] = ()
    if rule is not None and rule.rule_type == "READER_NEXT_SEQUENCE":
        selected = [
            sample
            for sample in samples
            if sample.action_sequence in {step.action_sequence for step in rule.steps}
        ]
        refs = tuple(dict.fromkeys(sample.event_id for sample in selected if sample.event_id))
        ordered = []
        for step in rule.steps:
            matches = [
                sample for sample in selected if sample.action_sequence == step.action_sequence
            ]
            if len(matches) != 1:
                break
            ordered.append(matches[0])
        known = (
            len(ordered) == len(rule.steps)
            and len(refs) == len(rule.steps)
            and all(
                sample.next_action_verified is True
                and type(sample.canonical_sequence) is int
                and sample.canonical_sequence > 0
                and not sample.capture_unknown
                and not sample.redacted
                and sample.phrase is not None
                and bool(sample.event_id)
                for sample in ordered
            )
            and all(
                left.canonical_sequence is not None
                and right.canonical_sequence is not None
                and left.canonical_sequence < right.canonical_sequence
                for left, right in zip(ordered, ordered[1:], strict=False)
            )
        )
        if known:
            return AssertionOutcome(
                assertion.assertion_id,
                assertion.kind,
                Condition.TRUE
                if all(
                    sample.phrase == step.phrase
                    for sample, step in zip(ordered, rule.steps, strict=True)
                )
                else Condition.FALSE,
                Provenance.EVALUATOR_DERIVED,
                tuple(sample.event_id for sample in ordered),
            )
        reason = (
            "consecutive successful NEXT actions or original ordered reader captures "
            "are missing, conflicting, unknown or redacted"
        )
    if rule is not None and rule.rule_type == "EXACT_READER_PHRASE":
        matches = [sample for sample in samples if sample.action_sequence == rule.action_sequence]
        if len(matches) == 1:
            sample = matches[0]
            refs = (sample.event_id,) if sample.event_id else ()
            if sample.capture_unknown or sample.redacted or sample.phrase is None or not refs:
                reason = "reader capture missing, unknown or redacted at the frozen action"
            else:
                return AssertionOutcome(
                    assertion.assertion_id,
                    assertion.kind,
                    Condition.TRUE if sample.phrase == rule.phrase else Condition.FALSE,
                    Provenance.EVALUATOR_DERIVED,
                    refs,
                )
        else:
            reason = "the frozen action has missing or conflicting reader observations"
    return AssertionOutcome(
        assertion.assertion_id,
        assertion.kind,
        Condition.UNKNOWN,
        Provenance.ABSENT if not refs else Provenance.EVALUATOR_DERIVED,
        refs,
        unknown_reason=reason,
    )


def observer_count_assertions(
    assertions: AssertionSet, *, effect: str, count: int | None
) -> list[dict[str, Any]]:
    """Called by the independent observer before it signs/admit its final effect source record.

    Return only conditions and unknown reasons, never expected counts/phrases. The authenticated
    containing event provides the evidence reference; this function cannot mint that provenance.
    """
    result: list[dict[str, Any]] = []
    for assertion in assertions.assertions:
        if assertion.kind is not AssertionKind.TASK_COMPLETION:
            continue
        rule = assertion.evaluation_rule
        known = (
            rule is not None
            and rule.rule_type == "EFFECT_COUNT"
            and rule.effect == effect
            and type(count) is int
            and count >= 0
        )
        condition = Condition.UNKNOWN
        if known:
            assert rule is not None
            condition = Condition.TRUE if count == rule.count else Condition.FALSE
        result.append(
            {
                "assertionId": assertion.assertion_id,
                "kind": assertion.kind.value,
                "condition": condition.value,
                "provenance": "OBSERVER_AUTHORED",
                **(
                    {"unknownReason": "measurement or matching frozen effect predicate unavailable"}
                    if not known
                    else {}
                ),
            }
        )
    return result


def functional_validation_assertions(
    assertions: AssertionSet, observation: ValidationObservation
) -> list[dict[str, Any]]:
    """Protected worker authors conditions before persistence, not the finalizer or navigator."""
    result: list[dict[str, Any]] = []
    for assertion in assertions.assertions:
        if assertion.kind is not AssertionKind.FUNCTIONAL_VALIDATION:
            continue
        rule = assertion.evaluation_rule
        known = (
            rule is not None
            and rule.rule_type == "PROTECTED_REFERENCE_VALIDATION"
            and rule.suite_digest == observation.suite_digest
        )
        condition = Condition.UNKNOWN
        if known:
            condition = Condition.TRUE if observation.passed else Condition.FALSE
        result.append(
            {
                "assertionId": assertion.assertion_id,
                "kind": assertion.kind.value,
                "condition": condition.value,
                "provenance": "OBSERVER_AUTHORED",
                **(
                    {"unknownReason": "matching frozen functional predicate unavailable"}
                    if not known
                    else {}
                ),
            }
        )
    return result
