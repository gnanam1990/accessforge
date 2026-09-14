"""Closed native keyboard-focus metadata inside an authenticated unresolved action observation.

This validates source claims and their action window, not physical truth or a focus predicate.
"""

from datetime import datetime, timedelta
from typing import Any

from accessforge_domain.journeys.assertions import KEYBOARD_FOCUS_ROLES
from accessforge_domain.timestamps import parse_rfc3339_utc

ROLES = KEYBOARD_FOCUS_ROLES


def validate(value: Any, *, dispatched_at: datetime, now: datetime) -> dict[str, Any]:
    record = validate_record(value)
    stamp = parse_rfc3339_utc(record["capturedAtUtc"])
    if not dispatched_at <= stamp <= now or now - stamp > timedelta(seconds=30):
        raise ValueError("keyboard focus capture is outside its original action window")
    return record


def validate_record(value: Any) -> dict[str, Any]:
    """Shape only for already-admitted historical sources; not a substitute for admission."""
    if not isinstance(value, dict):
        raise ValueError("keyboard focus metadata unavailable")
    common = {"measurementKind", "status", "capturedAtUtc"}
    known = value.get("status") == "KNOWN"
    expected = common | ({"role", "identifierDigest"} if known else {"reason"})
    if (
        set(value) != expected
        or value.get("measurementKind") != "AX_KEYBOARD_FOCUS"
        or value.get("status") not in {"KNOWN", "UNKNOWN"}
        or not isinstance(value.get("capturedAtUtc"), str)
        or len(value["capturedAtUtc"]) > 40
    ):
        raise ValueError("keyboard focus metadata malformed")
    parse_rfc3339_utc(value["capturedAtUtc"])
    if known:
        fingerprint = value["identifierDigest"]
        if (
            not isinstance(value["role"], str)
            or value["role"] not in ROLES
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
        ):
            raise ValueError("keyboard focus target metadata malformed")
    elif value["reason"] != "NATIVE_FOCUS_UNAVAILABLE":
        raise ValueError("keyboard focus unknown reason must be non-sensitive")
    return dict(value)
