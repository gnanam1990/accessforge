"""Synthetic retained-input joins, not live observer or physical execution proof."""

from copy import deepcopy
from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.reference_effect_scope import REFERENCE_EFFECT_POLICY_DIGEST
from accessforge_domain.states import Condition
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.reference_effect_evidence import CONDITION_FORMAT, observed_assertions


def inputs() -> tuple[dict[str, Any], AssertionSet]:
    assertions = AssertionSet(
        (
            Assertion(
                "done",
                AssertionKind.TASK_COMPLETION,
                "Done",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
            ),
            Assertion(
                "none",
                AssertionKind.FORBIDDEN_EFFECT,
                "No effect",
                unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
                evaluation_rule=EvaluationRule(
                    "CONTINUOUS_EFFECT_ABSENCE",
                    effect="CREATE_TEST_REQUEST",
                    scope_digest=REFERENCE_EFFECT_POLICY_DIGEST,
                ),
            ),
        )
    )
    common = {
        "assertionSetDigest": digest(assertions.canonical_form()),
        "environmentConfigurationDigest": "b" * 64,
        "fixtureInstanceId": "fixture",
    }
    lifecycle = {
        **common,
        "finalSample": False,
        "scopeDigest": "c" * 64,
        "clockEpoch": "epoch",
        "startNs": "10",
        "effect": "CREATE_TEST_REQUEST",
        "policyDigest": REFERENCE_EFFECT_POLICY_DIGEST,
    }

    def event(sequence: int, kind: str, source: dict[str, Any]) -> dict[str, Any]:
        return {
            "eventId": str(sequence),
            "sequence": sequence,
            "eventType": kind,
            "payload": {
                "producerId": "observer" if kind == "EFFECT_RECEIPT" else "supervisor",
                "serviceIdentity": "OBSERVER" if kind == "EFFECT_RECEIPT" else "SUPERVISOR",
                "sourceRecord": source,
            },
        }

    ready = event(
        1,
        "EFFECT_RECEIPT",
        {
            **lifecycle,
            "collectorPhase": "READY",
            "afterActionSequence": 0,
            "assertionObservations": [],
        },
    )
    closed = event(
        6,
        "EFFECT_RECEIPT",
        {
            **lifecycle,
            "collectorPhase": "CLOSED",
            "afterActionSequence": 2,
            "startEventId": "1",
            "conditionFormat": CONDITION_FORMAT,
            "endNs": "30",
            "intervals": [{"startNs": "10", "endNs": "30", "occurrences": "0"}],
            "assertionObservations": [
                {
                    "assertionId": "none",
                    "kind": "FORBIDDEN_EFFECT",
                    "condition": "TRUE",
                    "provenance": "OBSERVER_AUTHORED",
                }
            ],
        },
    )
    final = event(
        7,
        "EFFECT_RECEIPT",
        {
            **common,
            "finalSample": True,
            "afterActionSequence": 2,
        },
    )
    actions = [
        event(2, "ACTION_INTENT", {"actionId": "a", "sequence": 1, "action": "NEXT"}),
        event(3, "ACTION_RESULT", {"actionId": "a", "sequence": 1, "status": "SUCCEEDED"}),
        event(4, "ACTION_INTENT", {"actionId": "b", "sequence": 2, "action": "STOP"}),
        event(5, "ACTION_RESULT", {"actionId": "b", "sequence": 2, "status": "SUCCEEDED"}),
    ]
    return {
        "EFFECT_RECEIPT": {"records": [ready, closed, final]},
        "ACTION_TRACE": {"records": actions},
    }, assertions


@pytest.mark.parametrize("condition", ["TRUE", "FALSE", "UNKNOWN"])
def test_consumes_authored_condition_and_original_event_references(condition: str) -> None:
    snapshots, assertions = inputs()
    item = snapshots["EFFECT_RECEIPT"]["records"][1]["payload"]["sourceRecord"][
        "assertionObservations"
    ][0]
    item["condition"] = condition
    if condition == "UNKNOWN":
        item["unknownReason"] = "coverage unavailable"
    result = observed_assertions(snapshots, assertions)["none"]
    assert result.condition == Condition(condition)
    assert result.provenance.value == "OBSERVER_AUTHORED"
    assert result.evidence_refs == ("1", "6")


@pytest.mark.parametrize("mode", ["absent", "historical", "no-authored-condition"])
def test_missing_evidence_never_becomes_a_pass(mode: str) -> None:
    snapshots, assertions = inputs()
    records = snapshots["EFFECT_RECEIPT"]["records"]
    if mode == "absent":
        records[:] = records[-1:]
    elif mode == "historical":
        records[1]["payload"]["sourceRecord"].pop("conditionFormat")
    else:
        records[1]["payload"]["sourceRecord"]["assertionObservations"] = []
    assert observed_assertions(snapshots, assertions) == {}


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate-ready",
        "wrong-ready-link",
        "late-ready",
        "early-close",
        "after-final",
        "failed-stop",
        "not-stop",
        "early-stop",
        "extra-action",
        "unsettled-action",
        "wrong-action-id",
        "wrong-action-sequence",
        "wrong-action-principal",
        "bool-sequence",
        "fixture",
        "environment",
        "assertion-digest",
        "scope",
        "clock",
        "start-tick",
        "policy",
        "observer-principal",
        "producer",
        "wrong-phase",
        "format",
        "late-start-count",
        "wrong-close-count",
        "wrong-final-count",
        "not-final",
        "other-family",
        "duplicate-condition",
        "unknown-condition",
        "extra-condition-field",
    ],
)
def test_conflicting_retained_bindings_are_refused(fault: str) -> None:
    snapshots, assertions = inputs()
    records = snapshots["EFFECT_RECEIPT"]["records"]
    ready, closed, final = records
    start, end, last = [r["payload"]["sourceRecord"] for r in records]
    actions = snapshots["ACTION_TRACE"]["records"]
    item = end["assertionObservations"][0]
    if fault == "duplicate-ready":
        records.insert(1, deepcopy(ready))
    elif fault == "wrong-ready-link":
        end["startEventId"] = "foreign"
    elif fault == "late-ready":
        ready["sequence"] = 3
    elif fault == "early-close":
        closed["sequence"] = 4
    elif fault == "after-final":
        closed["sequence"] = 8
    elif fault in {"failed-stop", "not-stop", "early-stop"}:
        target, field, value = {
            "failed-stop": (-1, "status", "FAILED"),
            "not-stop": (-2, "action", "NEXT"),
            "early-stop": (0, "action", "STOP"),
        }[fault]
        actions[target]["payload"]["sourceRecord"][field] = value
    elif fault == "extra-action":
        actions.extend(deepcopy(actions[:2]))
    elif fault == "unsettled-action":
        actions.pop()
    elif fault in {"wrong-action-id", "wrong-action-sequence", "bool-sequence"}:
        field, replacement = {
            "wrong-action-id": ("actionId", "foreign"),
            "wrong-action-sequence": ("sequence", 3),
            "bool-sequence": ("sequence", True),
        }[fault]
        actions[1]["payload"]["sourceRecord"][field] = replacement
    elif fault == "wrong-action-principal":
        actions[0]["payload"]["serviceIdentity"] = "NAVIGATOR"
    elif fault in {
        "fixture",
        "environment",
        "assertion-digest",
        "scope",
        "clock",
        "start-tick",
        "policy",
        "wrong-phase",
        "format",
    }:
        field = {
            "fixture": "fixtureInstanceId",
            "environment": "environmentConfigurationDigest",
            "assertion-digest": "assertionSetDigest",
            "scope": "scopeDigest",
            "clock": "clockEpoch",
            "start-tick": "startNs",
            "policy": "policyDigest",
            "wrong-phase": "collectorPhase",
            "format": "conditionFormat",
        }[fault]
        end[field] = "foreign"
    elif fault in {"observer-principal", "producer"}:
        closed["payload"]["serviceIdentity" if fault == "observer-principal" else "producerId"] = (
            "foreign"
        )
    elif fault in {"late-start-count", "wrong-close-count", "wrong-final-count"}:
        {"late-start-count": start, "wrong-close-count": end, "wrong-final-count": last}[fault][
            "afterActionSequence"
        ] = 1
    elif fault == "not-final":
        last["finalSample"] = False
    elif fault == "other-family":
        item["assertionId"] = "done"
    elif fault == "duplicate-condition":
        end["assertionObservations"].append(deepcopy(item))
    elif fault == "unknown-condition":
        item["condition"] = "UNKNOWN"
    elif fault == "extra-condition-field":
        item["count"] = 0
    with pytest.raises(Refused):
        observed_assertions(snapshots, assertions)
