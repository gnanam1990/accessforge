"""Synthetic verified-input joins; no actual worker, retention or screen-reader attestation."""

from typing import Any

import pytest

from accessforge_domain.canonical import digest
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
from accessforge_domain.states import Condition
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.functional_evidence import observed_assertions


def inputs() -> dict[str, Any]:
    reasons = frozenset({UnknownReason.OBSERVATION_MISSING})
    assertions = AssertionSet(
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
        )
    )
    context = {
        "workspace_id": "workspace",
        "run_id": "run",
        "attempt_id": "attempt",
        "lease_id": "lease",
        "epoch": 2,
        "manifest_digest": "m" * 64,
    }
    observation = ValidationObservation(
        VALIDATION_SUITE_DIGEST, tuple((field, 422, 0) for field, _ in INVALID_VALUES)
    )
    receipt = {
        "format": "accessforge.functional-regression.v1",
        "workspaceId": "workspace",
        "runId": "run",
        "leaseId": "lease",
        "leaseEpoch": 2,
        "artifactDigest": "b" * 64,
        "producerReceipt": {
            "format": "accessforge.functional-producer.v1",
            "validation": observation.canonical_form(),
            "runEvidence": {
                "workspaceId": "workspace",
                "runId": "run",
                "leaseId": "lease",
                "leaseEpoch": 2,
                "manifestDigest": context["manifest_digest"],
                "assertionSetDigest": digest(assertions.canonical_form()),
                "assertionObservations": functional_validation_assertions(assertions, observation),
            },
        },
    }
    bundle = {
        "format": "accessforge.functional-regression-artifact.v1",
        "runId": "run",
        "attemptId": "attempt",
        "manifestDigest": context["manifest_digest"],
        "producerId": "functional-regression:attempt",
        "receipt": receipt,
        "receiptDigest": digest(receipt),
    }
    return {
        "bundle": bundle,
        "context": context,
        "assertions": assertions,
        "observed_build": "b" * 64,
        "artifact_digest": digest(bundle),
    }


def rehash(data: dict[str, Any]) -> None:
    data["bundle"]["receiptDigest"] = digest(data["bundle"]["receipt"])
    data["artifact_digest"] = digest(data["bundle"])


def test_original_authored_outcome_retains_provenance_and_artifact_reference() -> None:
    data = inputs()
    outcome = observed_assertions(**data)["validation"]
    assert outcome.condition is Condition.TRUE
    assert outcome.provenance.value == "OBSERVER_AUTHORED"
    assert outcome.evidence_refs == (data["artifact_digest"],)


@pytest.mark.parametrize("missing", ["bundle", "build", "producer", "run-binding", "condition"])
def test_missing_evidence_never_infers_a_functional_pass(missing: str) -> None:
    data = inputs()
    if missing == "bundle":
        data["bundle"] = None
    elif missing == "build":
        data["observed_build"] = None
    else:
        receipt = data["bundle"]["receipt"]
        if missing == "producer":
            receipt.pop("producerReceipt")
        elif missing == "run-binding":
            receipt["producerReceipt"]["runEvidence"] = None
        else:
            receipt["producerReceipt"]["runEvidence"]["assertionObservations"] = []
        rehash(data)
    assert observed_assertions(**data) == {}


@pytest.mark.parametrize(
    "field",
    ["workspaceId", "runId", "leaseId", "leaseEpoch", "manifestDigest", "assertionSetDigest"],
)
def test_foreign_original_binding_is_not_accepted_even_with_valid_hashes(field: str) -> None:
    data = inputs()
    data["bundle"]["receipt"]["producerReceipt"]["runEvidence"][field] = "foreign"
    rehash(data)
    with pytest.raises(Refused):
        observed_assertions(**data)


@pytest.mark.parametrize(
    "mutation",
    [
        "build",
        "artifact",
        "duplicate",
        "other-family",
        "provenance",
        "suite",
        "unknown-reason",
        "condition",
        "extra",
    ],
)
def test_contradictory_original_outcomes_refuse(mutation: str) -> None:
    data = inputs()
    producer = data["bundle"]["receipt"]["producerReceipt"]
    items = producer["runEvidence"]["assertionObservations"]
    if mutation == "build":
        data["observed_build"] = "c" * 64
    elif mutation == "duplicate":
        items.append(dict(items[0]))
    elif mutation == "other-family":
        items[0]["assertionId"] = "completion"
    elif mutation == "provenance":
        items[0]["provenance"] = "EVALUATOR_DERIVED"
    elif mutation == "suite":
        producer["validation"]["suiteDigest"] = "c" * 64
    elif mutation == "unknown-reason":
        items[0]["condition"] = "UNKNOWN"
    elif mutation == "condition":
        items[0]["condition"] = "PASS"
    elif mutation == "extra":
        items[0]["expectedCount"] = 0
    rehash(data)
    if mutation == "artifact":
        data["artifact_digest"] = "c" * 64
    with pytest.raises(Refused):
        observed_assertions(**data)
