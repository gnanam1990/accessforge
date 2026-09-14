"""Reader-only reconstruction checks; synthetic records are never physical evidence."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_orchestrator.navigator.projection import ProjectionRefused, _observation


def test_unknown_and_genuine_silence_are_not_conflated() -> None:
    identifier, now = str(uuid4()), datetime.now(UTC)
    action = {"id": identifier, "action_sequence": 1, "dispatched_at": now, "result_at": now}
    base = {"actionId": identifier, "actionSequence": 1, "capturedAtUtc": to_rfc3339_utc(now)}
    unknown = _observation(
        {**base, "provenance": "CAPTURE_UNKNOWN", "reason": "unavailable"}, action
    )
    silence = _observation({**base, "phrase": ""}, action)
    assert unknown.phrase is None and unknown.provenance == "CAPTURE_UNKNOWN"
    assert silence.phrase == "" and silence.provenance == "ACTUAL_READER"


def test_private_focus_metadata_is_verified_then_excluded_from_navigator() -> None:
    identifier, now = str(uuid4()), datetime.now(UTC)
    action = {"id": identifier, "action_sequence": 1, "dispatched_at": now, "result_at": now}
    source = {
        "actionId": identifier,
        "actionSequence": 1,
        "capturedAtUtc": to_rfc3339_utc(now),
        "phrase": "Name",
        "keyboardFocus": {
            "measurementKind": "AX_KEYBOARD_FOCUS",
            "status": "KNOWN",
            "capturedAtUtc": to_rfc3339_utc(now),
            "role": "AXTextField",
            "identifierDigest": "a" * 64,
        },
    }
    result = _observation(source, action)
    assert result.phrase == "Name"
    assert "keyboardFocus" not in result.model_dump()
    assert "identifierDigest" not in result.model_dump_json()
    with pytest.raises(ProjectionRefused):
        _observation({**source, "keyboardFocus": {"value": "private"}}, action)


@pytest.mark.parametrize(
    "change",
    [
        {"actionSequence": True},
        {"actionId": str(uuid4())},
        {"observerReceipt": "not an announcement"},
        {"capturedAtUtc": "2000-01-01T00:00:00Z"},
        {"phrase": "x" * 4097},
    ],
)
def test_changed_extra_or_oversized_reader_content_is_refused(change: dict[str, Any]) -> None:
    identifier, now = str(uuid4()), datetime.now(UTC)
    action = {"id": identifier, "action_sequence": 1, "dispatched_at": now, "result_at": now}
    source = {
        "actionId": identifier,
        "actionSequence": 1,
        "capturedAtUtc": to_rfc3339_utc(now),
        "phrase": "Name",
        **change,
    }
    with pytest.raises((ProjectionRefused, ValidationError)):
        _observation(source, action)
