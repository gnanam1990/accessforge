"""Evaluate explicitly frozen rules, never descriptions or caller-selected success criteria.

Reader values are evaluator-derived; effect values are produced on the independent observer side.
The callers must authenticate and bind retained source records before constructing these inputs.
This module does not turn unauthenticated JSON into evidence or decide a run outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def derive_reader_assertion(
    assertion: Assertion, samples: tuple[ReaderSample, ...]
) -> AssertionOutcome:
    rule = assertion.evaluation_rule
    reason = "no supported frozen reader predicate; descriptions are not executable expectations"
    refs: tuple[str, ...] = ()
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
