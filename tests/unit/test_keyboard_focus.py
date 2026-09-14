from datetime import UTC, datetime, timedelta

import pytest

from accessforge_persistence.keyboard_focus import validate

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
KNOWN = {
    "measurementKind": "AX_KEYBOARD_FOCUS",
    "status": "KNOWN",
    "capturedAtUtc": "2026-09-14T12:00:00Z",
    "role": "AXTextField",
    "identifierDigest": "a" * 64,
}


@pytest.mark.parametrize("unknown", [False, True])
def test_closed_focus_metadata_is_not_an_assertion(unknown: bool) -> None:
    value = (
        (
            {k: v for k, v in KNOWN.items() if k not in {"role", "identifierDigest"}}
            | {"status": "UNKNOWN", "reason": "NATIVE_FOCUS_UNAVAILABLE"}
        )
        if unknown
        else KNOWN
    )
    result = validate(value, dispatched_at=NOW - timedelta(seconds=1), now=NOW)
    assert result == value and result is not value
    assert "condition" not in result and "actionId" not in result


@pytest.mark.parametrize(
    "change",
    [
        {"role": "AXToolbar"},
        {"identifierDigest": "a"},
        {"value": "secret"},
        {"capturedAtUtc": "bad"},
        {"status": "PASS"},
        {"measurementKind": "SPEECH"},
        {"capturedAtUtc": "2026-09-14T11:59:58Z"},
        {"capturedAtUtc": "2026-09-14T12:00:01Z"},
    ],
)
def test_invalid_or_out_of_action_window_refuses(change: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        validate(KNOWN | change, dispatched_at=NOW - timedelta(seconds=1), now=NOW)


def test_capture_cannot_be_reused_after_freshness_window() -> None:
    with pytest.raises(ValueError):
        validate(KNOWN, dispatched_at=NOW, now=NOW + timedelta(seconds=31))
