"""Synthetic original-row policy checks. These do not establish a real evaluated reader pair."""

from copy import deepcopy
from typing import Any

import pytest

from accessforge_domain.evaluation.identity import IdentityKind
from accessforge_persistence.regression_attestation import (
    AUTHORIZATION_CHECKS,
    REQUIRED_CHECKS,
    _evaluated_pair,
)


def inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    identities = {kind.value: kind.value.lower() for kind in IdentityKind}
    baseline = {
        "outcome": "FAIL",
        "sealedIdentities": identities,
        "observedIdentities": dict(identities),
    }
    candidate: dict[str, Any] = {
        **deepcopy(baseline),
        "outcome": "PASS",
        "runId": "candidate",
        "manifestDigest": "manifest",
        "assertions": [
            {
                "assertionId": "validation",
                "kind": "FUNCTIONAL_VALIDATION",
                "condition": "TRUE",
                "provenance": "OBSERVER_AUTHORED",
                "evidenceRefs": ["artifact"],
            }
        ],
    }
    receipt = {
        "artifactDigest": "build",
        "checks": sorted(REQUIRED_CHECKS)
        + [prefix + str(index) for prefix in AUTHORIZATION_CHECKS for index in range(3)],
        "producerReceipt": {
            "runEvidence": {
                "runId": "candidate",
                "manifestDigest": "manifest",
                "assertionSetDigest": "assertion_set",
                "assertionObservations": [
                    {k: v for k, v in candidate["assertions"][0].items() if k != "evidenceRefs"}
                ],
            }
        },
    }
    return baseline, candidate, receipt, "artifact"


def test_complete_original_row_policy_accepts_only_its_referenced_functional_conditions() -> None:
    assert _evaluated_pair(*inputs())
    baseline, candidate, receipt, ref = inputs()
    candidate["outcome"] = "FAIL"  # Regression gate can pass while reader repair fails.
    assert _evaluated_pair(baseline, candidate, receipt, ref)


@pytest.mark.parametrize(
    "fault",
    [
        "baseline-pass",
        "candidate-unknown",
        "baseline-identity",
        "candidate-identity",
        "empty-identity",
        "build",
        "missing-check",
        "auth-coverage",
        "duplicate-check",
        "producer",
        "run",
        "manifest",
        "assertion-set",
        "missing-assertion",
        "unknown-assertion",
        "derived",
        "ref",
        "original-condition",
    ],
)
def test_missing_or_substituted_proof_never_opens_regression_gate(fault: str) -> None:
    baseline, candidate, receipt, ref = inputs()
    original = receipt["producerReceipt"]["runEvidence"]
    if fault == "baseline-pass":
        baseline["outcome"] = "PASS"
    elif fault == "candidate-unknown":
        candidate["outcome"] = "INCONCLUSIVE"
    elif fault == "baseline-identity":
        baseline["observedIdentities"].pop("RUNNER_PROFILE")
    elif fault == "candidate-identity":
        candidate["observedIdentities"]["BUILD"] = "different"
    elif fault == "empty-identity":
        baseline["observedIdentities"]["BUILD"] = baseline["sealedIdentities"]["BUILD"] = ""
    elif fault == "build":
        receipt["artifactDigest"] = "other"
    elif fault == "missing-check":
        receipt["checks"].remove("duplicate_conflict")
    elif fault == "auth-coverage":
        receipt["checks"].remove(AUTHORIZATION_CHECKS[0] + "0")
    elif fault == "duplicate-check":
        receipt["checks"].append(receipt["checks"][0])
    elif fault == "producer":
        receipt.pop("producerReceipt")
    elif fault == "run":
        original["runId"] = "other"
    elif fault == "manifest":
        original["manifestDigest"] = "other"
    elif fault == "assertion-set":
        original["assertionSetDigest"] = "other"
    elif fault == "missing-assertion":
        candidate["assertions"] = []
    elif fault == "unknown-assertion":
        candidate["assertions"][0]["condition"] = "UNKNOWN"
    elif fault == "derived":
        candidate["assertions"][0]["provenance"] = "EVALUATOR_DERIVED"
    elif fault == "ref":
        ref = "another-artifact"
    elif fault == "original-condition":
        original["assertionObservations"][0]["condition"] = "UNKNOWN"
    assert not _evaluated_pair(baseline, candidate, receipt, ref)
