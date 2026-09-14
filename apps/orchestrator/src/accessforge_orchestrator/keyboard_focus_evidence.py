"""Project original verified retained focus records; never infer identity from reader text.

Called only after finalizer artifact/canonical-source verification. Original server admission
checks the native timestamp against the unresolved action window; this historical projection
does not reconstruct that timestamp or manufacture fresh admission from today's clock.
"""

from typing import Any

from accessforge_domain.evaluation.rules import KeyboardFocusSample
from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_persistence.keyboard_focus import validate_record


def keyboard_focus_samples(snapshots: dict[str, Any]) -> tuple[KeyboardFocusSample, ...]:
    intents: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for event in snapshots["ACTION_TRACE"]["records"]:
        payload = event["payload"]
        if payload.get("serviceIdentity") != "SUPERVISOR" or event["eventType"] not in {
            "ACTION_INTENT",
            "ACTION_RESULT",
        }:
            raise Refused("native focus action provenance differs")
        destination = intents if event["eventType"] == "ACTION_INTENT" else results
        action_id = payload["sourceRecord"]["actionId"]
        if action_id in destination:
            raise Refused("duplicate native focus action boundary")
        destination[action_id] = event
    samples = []
    for event in snapshots["SPEECH_TRANSCRIPT"]["records"]:
        payload = event["payload"]
        source = payload["sourceRecord"]
        if (
            event["eventType"] != "READER_OBSERVATION"
            or payload.get("serviceIdentity") != "SUPERVISOR"
        ):
            raise Refused("native focus source provenance differs")
        focus = None
        if "keyboardFocus" in source:
            try:
                focus = validate_record(source["keyboardFocus"])
            except (ValueError, TypeError) as exc:
                raise Refused("retained native focus metadata malformed") from exc
        intent, result = intents.get(source["actionId"]), results.get(source["actionId"])
        verified = False
        if intent is not None and result is not None:
            command, completed = (
                intent["payload"]["sourceRecord"],
                result["payload"]["sourceRecord"],
            )
            verified = (
                isinstance(command.get("action"), str)
                and command["action"] in ALLOWED_ACTIONS - {"STOP"}
                and completed.get("status") == "SUCCEEDED"
                and type(source["actionSequence"]) is int
                and command.get("sequence") == source["actionSequence"] == completed.get("sequence")
                and intent["sequence"] < event["sequence"] < result["sequence"]
            )
        known = focus is not None and focus["status"] == "KNOWN"
        samples.append(
            KeyboardFocusSample(
                source["actionSequence"],
                event["eventId"],
                role=focus["role"] if known and focus is not None else None,
                identifier_digest=focus["identifierDigest"]
                if known and focus is not None
                else None,
                successful_action_verified=verified,
            )
        )
    return tuple(samples)
