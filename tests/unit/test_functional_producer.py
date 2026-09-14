"""Synthetic measurements test the producer contract, not actual HTTP/database/AT execution."""

from typing import Any

import pytest

from accessforge_domain.evaluation.rules import functional_validation_assertions
from accessforge_domain.functional_validation import (
    INVALID_VALUES,
    VALIDATION_SUITE_DIGEST,
    ValidationObservation,
)
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)


def assertions() -> AssertionSet:
    reasons = frozenset({UnknownReason.OBSERVATION_MISSING})
    return AssertionSet(
        (
            Assertion(
                "completion", AssertionKind.TASK_COMPLETION, "Complete", unknown_reasons=reasons
            ),
            Assertion(
                "validation",
                AssertionKind.FUNCTIONAL_VALIDATION,
                "Validate",
                unknown_reasons=reasons,
                evaluation_rule=EvaluationRule(
                    "PROTECTED_REFERENCE_VALIDATION", suite_digest=VALIDATION_SUITE_DIGEST
                ),
            ),
            Assertion(
                "prose", AssertionKind.FUNCTIONAL_VALIDATION, "Validate", unknown_reasons=reasons
            ),
        )
    )


@pytest.mark.parametrize(
    "status,count,condition", [(422, 0, "TRUE"), (200, 0, "FALSE"), (422, 1, "FALSE")]
)
def test_actual_measurements_determine_conditions_not_check_names(
    status: int, count: int, condition: str
) -> None:
    observation = ValidationObservation(
        VALIDATION_SUITE_DIGEST, tuple((field, status, count) for field, _ in INVALID_VALUES)
    )
    result = functional_validation_assertions(assertions(), observation)
    assert [item["assertionId"] for item in result] == ["validation", "prose"]
    assert result[0]["condition"] == condition
    assert result[0]["provenance"] == "OBSERVER_AUTHORED"
    assert result[1]["condition"] == "UNKNOWN"
    assert "unknownReason" in result[1]
    mutable = observation.canonical_form()
    mutable["cases"][0]["httpStatus"] = 599
    assert observation.cases[0][1] == status


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "order", "bool", "negative", "status", "list", "digest"]
)
def test_partial_malformed_or_different_suite_cannot_be_published(mutation: str) -> None:
    cases: Any = tuple((field, 422, 0) for field, _ in INVALID_VALUES)
    suite = VALIDATION_SUITE_DIGEST
    if mutation == "missing":
        cases = cases[:-1]
    elif mutation == "duplicate":
        cases = (cases[0],) * 4
    elif mutation == "order":
        cases = cases[::-1]
    elif mutation == "bool":
        cases = ((cases[0][0], 422, False), *cases[1:])
    elif mutation == "negative":
        cases = ((cases[0][0], 422, -1), *cases[1:])
    elif mutation == "status":
        cases = ((cases[0][0], 999, 0), *cases[1:])
    elif mutation == "list":
        cases = list(cases)
    elif mutation == "digest":
        suite = "0" * 64
    with pytest.raises(ValueError):
        ValidationObservation(suite, cases)


def test_artifact_only_functional_receipt_does_not_require_a_fabricated_stream() -> None:
    from accessforge_persistence.evidence.session import stream_requirements

    required = {
        kind: kind.lower()
        for kind in (
            "RUNNER_JOURNAL",
            "MODEL_RUNTIME",
            "FIXTURE_SETUP",
            "FUNCTIONAL_REGRESSION",
            "ACTION_TRACE",
            "SPEECH_TRANSCRIPT",
            "PREFLIGHT_RECORD",
            "EFFECT_RECEIPT",
        )
    }
    assert stream_requirements(required) == {
        kind: kind.lower()
        for kind in (
            "ACTION_TRACE",
            "SPEECH_TRANSCRIPT",
            "PREFLIGHT_RECORD",
            "EFFECT_RECEIPT",
        )
    }
    assert "FUNCTIONAL_REGRESSION" in required  # Still mandatory as an artifact.
