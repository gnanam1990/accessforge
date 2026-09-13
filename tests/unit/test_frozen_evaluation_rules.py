"""Explicit predicates and provenance; not physical reader or full-verdict proof."""

from dataclasses import replace

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.rules import (
    ReaderSample,
    derive_reader_assertion,
    observer_count_assertions,
)
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.states import Condition


def _assertions() -> AssertionSet:
    reasons = frozenset({UnknownReason.OBSERVATION_MISSING})
    return AssertionSet(
        (
            Assertion(
                "done",
                AssertionKind.TASK_COMPLETION,
                "One request",
                unknown_reasons=reasons,
                evaluation_rule=EvaluationRule(
                    "EFFECT_COUNT", effect="CREATE_TEST_REQUEST", count=1
                ),
            ),
            Assertion(
                "spoken",
                AssertionKind.REQUIRED_ANNOUNCEMENT,
                "Exact captured phrase",
                unknown_reasons=reasons,
                evaluation_rule=EvaluationRule(
                    "EXACT_READER_PHRASE", action_sequence=2, phrase="Email invalid"
                ),
            ),
        )
    )


def test_contract_round_trip_preserves_rules_and_legacy_identity() -> None:
    frozen = _assertions()
    assert AssertionSet.from_canonical_form(frozen.canonical_form()) == frozen
    changed = AssertionSet(
        (
            replace(
                frozen.assertions[0],
                evaluation_rule=EvaluationRule(
                    "EFFECT_COUNT", effect="CREATE_TEST_REQUEST", count=2
                ),
            ),
            frozen.assertions[1],
        )
    )
    assert digest(changed.canonical_form()) != digest(frozen.canonical_form())
    legacy = AssertionSet(tuple(replace(a, evaluation_rule=None) for a in frozen.assertions))
    old_items = legacy.canonical_form()["assertions"]
    assert isinstance(old_items, list)
    assert all("evaluationRule" not in a for a in old_items)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"type": "PASS"},
        {"type": "EFFECT_COUNT", "effect": "CREATE_TEST_REQUEST", "count": True},
        {"type": "EFFECT_COUNT", "effect": "CREATE_TEST_REQUEST", "count": -1},
        {"type": "EFFECT_COUNT", "effect": "SEND_PAYMENT", "count": 1},
        {"type": "EXACT_READER_PHRASE", "actionSequence": 0, "phrase": "Email invalid"},
        {"type": "EXACT_READER_PHRASE", "actionSequence": 2.5, "phrase": "Email invalid"},
        {"type": "EXACT_READER_PHRASE", "actionSequence": 2, "phrase": ""},
        {"type": "EXACT_READER_PHRASE", "actionSequence": 2, "phrase": "x", "regex": True},
    ],
)
def test_unsupported_predicates_are_not_coerced(bad: object) -> None:
    with pytest.raises(ValueError):
        EvaluationRule.parse(bad)


@pytest.mark.parametrize(
    "samples,expected",
    [
        ((ReaderSample(2, "event-2", "Email invalid"),), Condition.TRUE),
        ((ReaderSample(2, "event-2", "email invalid"),), Condition.FALSE),
        ((ReaderSample(2, "event-2", None, capture_unknown=True),), Condition.UNKNOWN),
        ((ReaderSample(2, "event-2", "Email invalid", redacted=True),), Condition.UNKNOWN),
        ((ReaderSample(1, "event-1", "Email invalid"),), Condition.UNKNOWN),
        (
            (
                ReaderSample(2, "event-2", "Email invalid"),
                ReaderSample(2, "event-3", "Email invalid"),
            ),
            Condition.UNKNOWN,
        ),
        ((), Condition.UNKNOWN),
    ],
)
def test_reader_rules_are_action_scoped_literal_and_missing_is_unknown(
    samples: tuple[ReaderSample, ...],
    expected: Condition,
) -> None:
    result = derive_reader_assertion(_assertions().assertions[1], samples)
    assert result.condition == expected
    assert result.provenance.value != "OBSERVER_AUTHORED"
    if expected is Condition.UNKNOWN:
        assert result.unknown_reason


@pytest.mark.parametrize(
    "count,condition",
    [(1, "TRUE"), (0, "FALSE"), (2, "FALSE"), (None, "UNKNOWN"), (True, "UNKNOWN")],
)
def test_only_observer_compares_frozen_effect_count(count: int | None, condition: str) -> None:
    result = observer_count_assertions(_assertions(), effect="CREATE_TEST_REQUEST", count=count)
    assert len(result) == 1 and result[0]["condition"] == condition
    assert result[0]["provenance"] == "OBSERVER_AUTHORED"
    assert "count" not in result[0] and "expected" not in result[0]


def test_no_prose_backfill_or_observer_substitution() -> None:
    frozen = _assertions()
    with pytest.raises(ValueError, match="observer"):
        replace(frozen.assertions[0], evaluation_rule=frozen.assertions[1].evaluation_rule)
    old = replace(frozen.assertions[1], evaluation_rule=None)
    assert (
        derive_reader_assertion(old, (ReaderSample(2, "event", "Email invalid"),)).condition
        is Condition.UNKNOWN
    )
    malformed = frozen.canonical_form()
    items = malformed["assertions"]
    assert isinstance(items, list)
    items[0]["observer"] = "READER"
    with pytest.raises(ValueError, match="observer"):
        AssertionSet.from_canonical_form(malformed)
