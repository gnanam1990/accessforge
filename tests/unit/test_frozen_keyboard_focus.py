"""Synthetic source joins and scoped predicates, not actual Safari or VoiceOver proof."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.rules import derive_keyboard_focus_assertion
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.states import Condition
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.keyboard_focus_evidence import keyboard_focus_samples


def assertion() -> Assertion:
    return Assertion(
        "focus",
        AssertionKind.FOCUS_BEHAVIOUR,
        "Exact native keyboard target",
        unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
        evaluation_rule=EvaluationRule(
            "EXACT_NATIVE_KEYBOARD_FOCUS",
            action_sequence=1,
            role="AXTextField",
            identifier_digest="a" * 64,
        ),
    )


def streams() -> dict[str, Any]:
    def event(kind: str, sequence: int, source: dict[str, Any]) -> dict[str, Any]:
        return {
            "eventType": kind,
            "sequence": sequence,
            "eventId": f"event-{sequence}",
            "payload": {"serviceIdentity": "SUPERVISOR", "sourceRecord": source},
        }

    return {
        "ACTION_TRACE": {
            "records": [
                event("ACTION_INTENT", 1, {"actionId": "one", "sequence": 1, "action": "NEXT"}),
                event(
                    "ACTION_RESULT", 3, {"actionId": "one", "sequence": 1, "status": "SUCCEEDED"}
                ),
            ]
        },
        "SPEECH_TRANSCRIPT": {
            "records": [
                event(
                    "READER_OBSERVATION",
                    2,
                    {
                        "actionId": "one",
                        "actionSequence": 1,
                        "phrase": "unrelated speech",
                        "keyboardFocus": {
                            "measurementKind": "AX_KEYBOARD_FOCUS",
                            "status": "KNOWN",
                            "capturedAtUtc": "2026-09-14T00:00:00Z",
                            "role": "AXTextField",
                            "identifierDigest": "a" * 64,
                        },
                    },
                )
            ]
        },
    }


def test_rule_roundtrip_digest_and_authority() -> None:
    rule = assertion().evaluation_rule
    assert rule is not None
    assert EvaluationRule.parse(rule.canonical_form()) == rule
    assert digest(replace(rule, identifier_digest="b" * 64).canonical_form()) != digest(
        rule.canonical_form()
    )
    with pytest.raises(ValueError, match="observer"):
        replace(assertion(), kind=AssertionKind.REQUIRED_ANNOUNCEMENT)
    with pytest.raises(ValueError):
        replace(rule, rule_type="EXACT_READER_PHRASE", phrase="target")


@pytest.mark.parametrize(
    "change",
    [
        {"actionSequence": True},
        {"actionSequence": 0},
        {"actionSequence": 1001},
        {"role": "AXWindow"},
        {"role": []},
        {"identifierDigest": "A" * 64},
        {"identifierDigest": "a" * 63},
        {"selector": "#email"},
        {"phrase": "Email"},
    ],
)
def test_closed_focus_contract(change: dict[str, Any]) -> None:
    rule = assertion().evaluation_rule
    assert rule is not None
    with pytest.raises(ValueError):
        EvaluationRule.parse({**rule.canonical_form(), **change})


@pytest.mark.parametrize(
    "fault,expected",
    [
        (None, Condition.TRUE),
        ("mismatch", Condition.FALSE),
        ("role", Condition.FALSE),
        ("missing", Condition.UNKNOWN),
        ("unknown", Condition.UNKNOWN),
        ("duplicate", Condition.UNKNOWN),
        ("ambiguous", Condition.UNKNOWN),
        ("stop", Condition.UNKNOWN),
        ("sequence", Condition.UNKNOWN),
        ("ordering", Condition.UNKNOWN),
        ("no-result", Condition.UNKNOWN),
        ("legacy-rule", Condition.UNKNOWN),
    ],
)
def test_only_original_successful_action_and_known_native_identity_decide(
    fault: str | None,
    expected: Condition,
) -> None:
    data = streams()
    actions = data["ACTION_TRACE"]["records"]
    speech = data["SPEECH_TRANSCRIPT"]["records"]
    source = speech[0]["payload"]["sourceRecord"]
    frozen = assertion()
    if fault == "mismatch":
        source["keyboardFocus"]["identifierDigest"] = "b" * 64
    elif fault == "role":
        source["keyboardFocus"]["role"] = "AXButton"
    elif fault == "missing":
        del source["keyboardFocus"]
    elif fault == "unknown":
        source["keyboardFocus"] = {
            "measurementKind": "AX_KEYBOARD_FOCUS",
            "status": "UNKNOWN",
            "capturedAtUtc": "2026-09-14T00:00:00Z",
            "reason": "NATIVE_FOCUS_UNAVAILABLE",
        }
    elif fault == "duplicate":
        speech.append(deepcopy(speech[0]))
    elif fault == "ambiguous":
        actions[1]["payload"]["sourceRecord"]["status"] = "AMBIGUOUS"
    elif fault == "stop":
        actions[0]["payload"]["sourceRecord"]["action"] = "STOP"
    elif fault == "sequence":
        actions[1]["payload"]["sourceRecord"]["sequence"] = 2
    elif fault == "ordering":
        speech[0]["sequence"] = 4
    elif fault == "no-result":
        actions.pop()
    elif fault == "legacy-rule":
        frozen = replace(frozen, evaluation_rule=None)
    result = derive_keyboard_focus_assertion(frozen, keyboard_focus_samples(data))
    assert result.condition is expected
    assert result.provenance.value != "OBSERVER_AUTHORED"
    if expected is not Condition.UNKNOWN:
        assert result.evidence_refs == ("event-2",)


def test_invalid_source_is_refused_and_speech_is_not_the_measurement() -> None:
    data = streams()
    source = data["SPEECH_TRANSCRIPT"]["records"][0]["payload"]["sourceRecord"]
    del source["phrase"]
    source.update(provenance="CAPTURE_UNKNOWN", reason="speech unavailable")
    assert (
        derive_keyboard_focus_assertion(assertion(), keyboard_focus_samples(data)).condition
        is Condition.TRUE
    )
    source["keyboardFocus"]["value"] = "private input"
    with pytest.raises(Refused):
        keyboard_focus_samples(data)
