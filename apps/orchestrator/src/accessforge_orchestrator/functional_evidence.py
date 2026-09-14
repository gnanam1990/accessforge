"""Consume original retained worker conditions; never author backend outcomes in the evaluator.

Call only after retained bytes, producer and historical cleanup bindings have been verified.
Missing runtime build evidence does not borrow identity from the regression's expected artifact.
"""

from __future__ import annotations

from typing import Any

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.assertions import AssertionOutcome, Provenance, ProvenanceError
from accessforge_domain.journeys.assertions import AssertionKind, AssertionSet
from accessforge_domain.states import Condition
from accessforge_persistence.functional_regression_evidence import producer

from .execution_artifacts import Refused


def observed_assertions(
    *,
    bundle: dict[str, Any] | None,
    context: dict[str, Any],
    assertions: AssertionSet,
    observed_build: str | None,
    artifact_digest: str | None,
) -> dict[str, AssertionOutcome]:
    if bundle is None or observed_build is None:
        return {}
    receipt = bundle.get("receipt")
    if (
        bundle.get("format") != "accessforge.functional-regression-artifact.v1"
        or bundle.get("runId") != str(context["run_id"])
        or bundle.get("attemptId") != str(context["attempt_id"])
        or bundle.get("manifestDigest") != context["manifest_digest"]
        or bundle.get("producerId") != producer(str(context["attempt_id"]))
        or not isinstance(receipt, dict)
        or bundle.get("receiptDigest") != digest(receipt)
        or artifact_digest is None
        or artifact_digest != digest(bundle)
    ):
        raise Refused("functional artifact does not match the verified execution")
    if (
        receipt.get("format") != "accessforge.functional-regression.v1"
        or receipt.get("workspaceId") != str(context["workspace_id"])
        or receipt.get("runId") != str(context["run_id"])
        or receipt.get("leaseId") != str(context["lease_id"])
        or type(receipt.get("leaseEpoch")) is not int
        or receipt.get("leaseEpoch") != context["epoch"]
        or receipt.get("artifactDigest") != observed_build
    ):
        raise Refused("functional receipt differs from the observed build or original lease")
    authored = receipt.get("producerReceipt")
    if authored is None:
        return {}  # Historical check names are not upgraded into producer-authored conditions.
    if (
        not isinstance(authored, dict)
        or authored.get("format") != "accessforge.functional-producer.v1"
    ):
        raise Refused("functional producer receipt malformed")
    binding = authored.get("runEvidence")
    if binding is None:
        return {}
    if (
        not isinstance(binding, dict)
        or binding.get("workspaceId") != str(context["workspace_id"])
        or binding.get("runId") != str(context["run_id"])
        or binding.get("leaseId") != str(context["lease_id"])
        or type(binding.get("leaseEpoch")) is not int
        or binding.get("leaseEpoch") != context["epoch"]
        or binding.get("manifestDigest") != context["manifest_digest"]
        or binding.get("assertionSetDigest") != digest(assertions.canonical_form())
    ):
        raise Refused("functional conditions lack their original frozen assertion binding")
    items = binding.get("assertionObservations")
    validation = authored.get("validation")
    if not isinstance(items, list) or not isinstance(validation, dict):
        raise Refused("functional conditions or suite identity unavailable")
    allowed = {
        a.assertion_id: a
        for a in assertions.assertions
        if a.kind is AssertionKind.FUNCTIONAL_VALIDATION
    }
    values: dict[str, AssertionOutcome] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("assertionId"), str):
            raise Refused("functional condition malformed")
        key = item["assertionId"]
        if (
            key not in allowed
            or key in values
            or item.get("kind") != "FUNCTIONAL_VALIDATION"
            or item.get("provenance") != "OBSERVER_AUTHORED"
        ):
            raise Refused(
                "functional producer cannot supply duplicate or other observer conditions"
            )
        expected_fields = {"assertionId", "kind", "condition", "provenance"}
        if item.get("condition") == "UNKNOWN":
            expected_fields.add("unknownReason")
            if not isinstance(item.get("unknownReason"), str) or not item["unknownReason"].strip():
                raise Refused("unknown functional condition requires its original reason")
        if set(item) != expected_fields:
            raise Refused("functional condition contains unsupported fields")
        rule = allowed[key].evaluation_rule
        if item.get("condition") != "UNKNOWN" and (
            rule is None
            or rule.rule_type != "PROTECTED_REFERENCE_VALIDATION"
            or rule.suite_digest != validation.get("suiteDigest")
        ):
            raise Refused("functional condition does not use the originally frozen suite")
        try:
            values[key] = AssertionOutcome(
                key,
                AssertionKind.FUNCTIONAL_VALIDATION,
                Condition(item["condition"]),
                Provenance.OBSERVER_AUTHORED,
                (artifact_digest,),
                item.get("unknownReason"),
            )
        except (KeyError, ValueError, ProvenanceError) as exc:
            raise Refused("functional condition is not a supported tri-state value") from exc
    return values
