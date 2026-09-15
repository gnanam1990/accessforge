"""Synthetic scope resolution, not an authenticated collector or execution receipt."""

from dataclasses import replace

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.effect_monitor import EffectCoverage, EffectInterval, EffectWindow
from accessforge_domain.evaluation.rules import reference_effect_monitor_assertions
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.reference_effect_scope import (
    REFERENCE_EFFECT_POLICY_DIGEST,
    ReferenceEffectBinding,
)

RUN = "11111111-1111-4111-8111-111111111111"
ATTEMPT = "22222222-2222-4222-8222-222222222222"
INSTALLATION = "33333333-3333-4333-8333-333333333333"
CLOCK = "44444444-4444-4444-8444-444444444444"


def policy(scope: str = REFERENCE_EFFECT_POLICY_DIGEST) -> AssertionSet:
    return AssertionSet(
        (
            Assertion(
                "no-request",
                AssertionKind.FORBIDDEN_EFFECT,
                "No request in the reserved fixture",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
                evaluation_rule=EvaluationRule(
                    "CONTINUOUS_EFFECT_ABSENCE",
                    effect="CREATE_TEST_REQUEST",
                    scope_digest=scope,
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


def binding(nonce: str = "synthetic-fixture-001") -> ReferenceEffectBinding:
    return ReferenceEffectBinding(RUN, ATTEMPT, INSTALLATION, nonce)


def window(resource: ReferenceEffectBinding) -> EffectWindow:
    return EffectWindow(
        RUN,
        ATTEMPT,
        resource.measurement_scope_digest(),
        CLOCK,
        "CREATE_TEST_REQUEST",
        10,
        30,
    )


def test_frozen_policy_survives_fresh_nonce_without_rewriting_assertions() -> None:
    contract = policy()
    frozen = digest(contract.canonical_form())
    scopes = set()
    for nonce in ("synthetic-fixture-001", "synthetic-fixture-002"):
        resource = binding(nonce)
        target = window(resource)
        scopes.add(target.scope_digest)
        result = reference_effect_monitor_assertions(
            contract,
            expected=target,
            binding=resource,
            measured=EffectCoverage(target, (EffectInterval(10, 30, 0),)),
        )
        assert result[0]["condition"] == "TRUE"
        assert digest(contract.canonical_form()) == frozen
    assert len(scopes) == 2
    assert REFERENCE_EFFECT_POLICY_DIGEST not in scopes


@pytest.mark.parametrize("damage", ["run", "attempt", "installation", "nonce", "effect", "policy"])
def test_scope_resolution_does_not_relabel_a_different_source(damage: str) -> None:
    resource = binding()
    target = window(resource)
    contract = policy()
    if damage == "run":
        resource = replace(resource, run_id=CLOCK)
    elif damage == "attempt":
        resource = replace(resource, attempt_id=CLOCK)
    elif damage == "installation":
        resource = replace(resource, installation_id=CLOCK)
    elif damage == "nonce":
        resource = binding("synthetic-fixture-002")
    elif damage == "effect":
        target = replace(target, effect="SEND_EMAIL")
    else:
        contract = policy(target.scope_digest)
    result = reference_effect_monitor_assertions(
        contract,
        expected=target,
        binding=resource,
        measured=EffectCoverage(target, (EffectInterval(10, 30, 0),)),
    )
    assert result[0]["condition"] == "UNKNOWN"


@pytest.mark.parametrize(
    "intervals,condition",
    [
        (None, "UNKNOWN"),
        ((), "UNKNOWN"),
        ((EffectInterval(10, 20, 0),), "UNKNOWN"),
        ((EffectInterval(10, 20, 1),), "FALSE"),
    ],
)
def test_resolved_policy_preserves_missing_coverage_and_positive_effects(
    intervals: tuple[EffectInterval, ...] | None,
    condition: str,
) -> None:
    resource = binding()
    target = window(resource)
    result = reference_effect_monitor_assertions(
        policy(),
        expected=target,
        binding=resource,
        measured=None if intervals is None else EffectCoverage(target, intervals),
    )
    assert result[0]["condition"] == condition
