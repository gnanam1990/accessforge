"""Frozen policy and independent measurement composition, not authenticated source proof."""

from dataclasses import replace

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.effect_monitor import EffectCoverage, EffectInterval, EffectWindow
from accessforge_domain.evaluation.rules import effect_monitor_assertions, observer_count_assertions
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)


def contract() -> AssertionSet:
    return AssertionSet(
        (
            Assertion(
                "no-mail",
                AssertionKind.FORBIDDEN_EFFECT,
                "No outbound email in the protected scope",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
                evaluation_rule=EvaluationRule(
                    "CONTINUOUS_EFFECT_ABSENCE", effect="SEND_EMAIL", scope_digest="a" * 64
                ),
            ),
            Assertion(
                "done",
                AssertionKind.TASK_COMPLETION,
                "Required completion",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
            ),
        )
    )


def window() -> EffectWindow:
    return EffectWindow(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "a" * 64,
        "33333333-3333-4333-8333-333333333333",
        "SEND_EMAIL",
        10,
        30,
    )


def test_frozen_scope_roundtrip_and_digest_binding() -> None:
    original = contract()
    assert AssertionSet.from_canonical_form(original.canonical_form()) == original
    changed = AssertionSet(
        (
            replace(
                original.assertions[0],
                evaluation_rule=EvaluationRule(
                    "CONTINUOUS_EFFECT_ABSENCE",
                    effect="SEND_EMAIL",
                    scope_digest="b" * 64,
                ),
            ),
            original.assertions[1],
        )
    )
    assert digest(original.canonical_form()) != digest(changed.canonical_form())


@pytest.mark.parametrize(
    "intervals,condition",
    [
        (None, "UNKNOWN"),
        ((), "UNKNOWN"),
        ((EffectInterval(10, 30, 0),), "TRUE"),
        ((EffectInterval(10, 20, 0),), "UNKNOWN"),
        ((EffectInterval(10, 20, 1),), "FALSE"),
    ],
)
def test_frozen_policy_uses_coverage_not_description(
    intervals: tuple[EffectInterval, ...] | None,
    condition: str,
) -> None:
    target = window()
    measured = None if intervals is None else EffectCoverage(target, intervals)
    result = effect_monitor_assertions(contract(), expected=target, measured=measured)
    assert result[0]["condition"] == condition
    assert result[0]["provenance"] == "OBSERVER_AUTHORED"
    assert ("unknownReason" in result[0]) is (condition == "UNKNOWN")
    assert "scopeDigest" not in result[0]


def test_other_scope_and_legacy_prose_cannot_claim_absence() -> None:
    for target in (
        replace(window(), scope_digest="b" * 64),
        replace(window(), effect="SEND_PAYMENT"),
    ):
        measured = EffectCoverage(target, (EffectInterval(10, 30, 0),))
        assert (
            effect_monitor_assertions(contract(), expected=target, measured=measured)[0][
                "condition"
            ]
            == "UNKNOWN"
        )
    legacy = AssertionSet(
        (replace(contract().assertions[0], evaluation_rule=None), contract().assertions[1])
    )
    assert (
        effect_monitor_assertions(
            legacy,
            expected=window(),
            measured=EffectCoverage(window(), (EffectInterval(10, 30, 0),)),
        )[0]["condition"]
        == "UNKNOWN"
    )
    assert all(
        item["assertionId"] != "no-mail"
        for item in observer_count_assertions(contract(), effect="SEND_EMAIL", count=0)
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "CONTINUOUS_EFFECT_ABSENCE", "effect": "SEND_EMAIL", "scopeDigest": "a"},
        {"type": "CONTINUOUS_EFFECT_ABSENCE", "effect": "ANY", "scopeDigest": "a" * 64},
        {
            "type": "CONTINUOUS_EFFECT_ABSENCE",
            "effect": "SEND_EMAIL",
            "scopeDigest": "a" * 64,
            "count": 0,
        },
        {"type": "EFFECT_COUNT", "effect": "CREATE_TEST_REQUEST", "scopeDigest": "a" * 64},
    ],
)
def test_malformed_and_cross_kind_rules_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        EvaluationRule.parse(bad)


def test_task_completion_cannot_use_effect_monitor_rule() -> None:
    with pytest.raises(ValueError):
        replace(contract().assertions[0], kind=AssertionKind.TASK_COMPLETION)
