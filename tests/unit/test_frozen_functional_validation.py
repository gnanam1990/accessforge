"""Frozen authoring contract only; no functional producer or actual reader verdict."""

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.functional_validation import VALIDATION_SUITE_DIGEST, validation_contract
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
    evaluation_rule_capabilities,
)


def test_exact_suite_is_frozen_and_only_functional_observer_can_use_it() -> None:
    literal = {"type": "PROTECTED_REFERENCE_VALIDATION", "suiteDigest": VALIDATION_SUITE_DIGEST}
    rule = EvaluationRule.parse(literal)
    assert rule.canonical_form() == literal
    reasons = frozenset({UnknownReason.OBSERVATION_MISSING})
    assertions = AssertionSet(
        (
            Assertion(
                "completion",
                AssertionKind.TASK_COMPLETION,
                "Request persisted",
                unknown_reasons=reasons,
            ),
            Assertion(
                "validation",
                AssertionKind.FUNCTIONAL_VALIDATION,
                "Server rejects invalid inputs",
                unknown_reasons=reasons,
                evaluation_rule=rule,
            ),
        )
    )
    assert AssertionSet.from_canonical_form(assertions.canonical_form()) == assertions
    assert (
        evaluation_rule_capabilities()["PROTECTED_REFERENCE_VALIDATION"]["suiteDigest"]
        == VALIDATION_SUITE_DIGEST
    )
    for kind in AssertionKind:
        if kind is not AssertionKind.FUNCTIONAL_VALIDATION:
            with pytest.raises(ValueError):
                Assertion(
                    "wrong", kind, "Wrong observer", unknown_reasons=reasons, evaluation_rule=rule
                )


def test_suite_values_and_results_are_not_caller_selected_or_shared_mutable_state() -> None:
    contract = validation_contract()
    assert digest(contract) == VALIDATION_SUITE_DIGEST
    assert [(case["field"], case["value"]) for case in contract["invalidCases"]] == [
        ("email", "not-an-email"),
        ("full_name", ""),
        ("category", "forbidden"),
        ("description", "short"),
    ]
    assert all(
        case["httpStatus"] == 422 and case["persistedRequests"] == 0
        for case in contract["invalidCases"]
    )
    contract["invalidCases"][0]["httpStatus"] = 200
    assert digest(contract) != VALIDATION_SUITE_DIGEST
    assert digest(validation_contract()) == VALIDATION_SUITE_DIGEST
    for value in (
        {"type": "PROTECTED_REFERENCE_VALIDATION", "suiteDigest": "0" * 64},
        {"type": "PROTECTED_REFERENCE_VALIDATION"},
        {
            "type": "PROTECTED_REFERENCE_VALIDATION",
            "suiteDigest": VALIDATION_SUITE_DIGEST,
            "count": 0,
        },
        {"type": "EFFECT_COUNT", "suiteDigest": VALIDATION_SUITE_DIGEST},
    ):
        with pytest.raises(ValueError):
            EvaluationRule.parse(value)
    with pytest.raises(ValueError):
        EvaluationRule(
            "PROTECTED_REFERENCE_VALIDATION", suite_digest=VALIDATION_SUITE_DIGEST, phrase="PASS"
        )


def test_existing_rule_canonical_bytes_are_unchanged() -> None:
    rule = EvaluationRule("EXACT_READER_PHRASE", action_sequence=1, phrase="Error")
    assert rule.canonical_form() == {
        "type": "EXACT_READER_PHRASE",
        "actionSequence": 1,
        "phrase": "Error",
    }
    with pytest.raises(ValueError):
        EvaluationRule(
            "EXACT_READER_PHRASE",
            action_sequence=1,
            phrase="Error",
            suite_digest=VALIDATION_SUITE_DIGEST,
        )
