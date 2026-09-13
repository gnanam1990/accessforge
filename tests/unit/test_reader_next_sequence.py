"""CI-only synthetic original streams; not an actual reader or full run acceptance claim."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.rules import derive_reader_assertion
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.states import Condition
from accessforge_orchestrator.runtime_evidence import reader_samples


def rule() -> dict[str, Any]:
    return {
        "type": "READER_NEXT_SEQUENCE",
        "steps": [
            {"actionSequence": 2, "phrase": "Name, edit text"},
            {"actionSequence": 3, "phrase": "Email, edit text"},
        ],
    }


def assertion() -> Assertion:
    return Assertion(
        "order",
        AssertionKind.READING_ORDER,
        "Name then email on consecutive NEXT actions",
        unknown_reasons=frozenset({UnknownReason.OBSERVATION_MISSING}),
        evaluation_rule=EvaluationRule.parse(rule()),
    )


def streams() -> dict[str, Any]:
    actions, speech = [], []
    for step in rule()["steps"]:
        n = step["actionSequence"]
        for kind, offset, details in (
            ("ACTION_INTENT", 0, {"action": "NEXT"}),
            ("ACTION_RESULT", 2, {"status": "SUCCEEDED"}),
        ):
            actions.append(
                {
                    "eventType": kind,
                    "sequence": n * 3 + offset,
                    "payload": {
                        "serviceIdentity": "SUPERVISOR",
                        "sourceRecord": {"actionId": str(n), "sequence": n, **details},
                    },
                }
            )
        source = {"actionId": str(n), "actionSequence": n, "phrase": step["phrase"]}
        speech.append(
            {
                "eventType": "READER_OBSERVATION",
                "sequence": n * 3 + 1,
                "eventId": f"speech-{n}",
                "payload": {
                    "serviceIdentity": "SUPERVISOR",
                    "sourceRecord": source,
                    "sourceRecordDigest": digest(source),
                    "submittedSourceRecordDigest": digest(source),
                },
            }
        )
    return {"ACTION_TRACE": {"records": actions}, "SPEECH_TRANSCRIPT": {"records": speech}}


def test_sequence_roundtrip_is_immutable_bounded_and_bound_to_reader_order() -> None:
    parsed = EvaluationRule.parse(rule())
    assert parsed.canonical_form() == rule()
    supplied = rule()
    frozen = EvaluationRule.parse(supplied)
    supplied["steps"][0]["phrase"] = "Changed"
    assert frozen == parsed and digest(frozen.canonical_form()) != digest(supplied)
    malformed = []
    for steps in (
        [],
        [rule()["steps"][0]],
        [*rule()["steps"], {"actionSequence": 5, "phrase": "Skipped"}],
        [{"actionSequence": True, "phrase": "Bool"}, rule()["steps"][1]],
        [{"actionSequence": 2, "phrase": "Name", "selector": "#name"}, rule()["steps"][1]],
    ):
        malformed.append({"type": "READER_NEXT_SEQUENCE", "steps": steps})
    for bad in malformed:
        with pytest.raises(ValueError):
            EvaluationRule.parse(bad)
    with pytest.raises(ValueError, match="observer"):
        replace(assertion(), kind=AssertionKind.FOCUS_BEHAVIOUR)


def test_complete_original_next_sequence_has_traceable_literal_values() -> None:
    samples = reader_samples(streams())
    result = derive_reader_assertion(assertion(), samples)
    assert result.condition is Condition.TRUE
    assert result.provenance.value == "EVALUATOR_DERIVED"
    assert result.evidence_refs == ("speech-2", "speech-3")
    changed = (
        replace(samples[0], phrase="Email, edit text"),
        replace(samples[1], phrase="Name, edit text"),
    )
    assert derive_reader_assertion(assertion(), changed).condition is Condition.FALSE
    assert (
        derive_reader_assertion(assertion(), (replace(samples[0], phrase=""), samples[1])).condition
        is Condition.FALSE
    )


def test_incomplete_wrong_action_and_untrusted_capture_stay_unknown() -> None:
    baseline = streams()
    mutations = []
    for key, value in (("action", "READ_CURRENT"), ("action", "PREVIOUS")):
        changed = deepcopy(baseline)
        changed["ACTION_TRACE"]["records"][0]["payload"]["sourceRecord"][key] = value
        mutations.append(changed)
    changed = deepcopy(baseline)
    changed["ACTION_TRACE"]["records"][1]["payload"]["sourceRecord"]["status"] = "FAILED"
    mutations.append(changed)
    for field, source_value in (("actionId", "foreign"), ("actionSequence", 20), ("phrase", None)):
        changed = deepcopy(baseline)
        changed["SPEECH_TRANSCRIPT"]["records"][0]["payload"]["sourceRecord"][field] = source_value
        mutations.append(changed)
    changed = deepcopy(baseline)
    changed["SPEECH_TRANSCRIPT"]["records"][0]["sequence"] = 99
    mutations.append(changed)
    samples = reader_samples(baseline)
    for observed in (
        samples[:1],
        samples + (samples[0],),
        (replace(samples[0], redacted=True), samples[1]),
        (replace(samples[0], capture_unknown=True), samples[1]),
        (replace(samples[0], next_action_verified=False), samples[1]),
    ):
        assert derive_reader_assertion(assertion(), observed).condition is Condition.UNKNOWN
    for records in mutations:
        result = derive_reader_assertion(assertion(), reader_samples(records))
        assert result.condition is Condition.UNKNOWN and result.unknown_reason
