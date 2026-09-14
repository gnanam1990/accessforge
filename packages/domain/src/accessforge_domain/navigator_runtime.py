"""Closed, content-free model runtime receipts. These are client observations, not attestations."""

from __future__ import annotations

import re
from typing import Any

from .navigator_model import validate_profile

MEANING = "SDK_REQUEST_CONFIGURATION_AND_RESPONSE_NOT_PROVIDER_MODEL_ATTESTATION"


def validate_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"meaning", "profile", "requests"}:
        raise ValueError("closed navigator runtime observation required")
    if value["meaning"] != MEANING or not isinstance(value["profile"], dict):
        raise ValueError("navigator runtime meaning or profile unavailable")
    validate_profile(value["profile"])
    requests = value["requests"]
    if (
        not isinstance(requests, list)
        or not 1 <= len(requests) <= value["profile"]["model_attempts"]
    ):
        raise ValueError("bounded actual provider requests required")
    for request in requests:
        if (
            not isinstance(request, dict)
            or set(request) != {"requestId", "httpStatus", "streamCompleted"}
            or not isinstance(request["requestId"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request["requestId"]) is None
            or type(request["httpStatus"]) is not int
            or not (request["httpStatus"] == 200 or 400 <= request["httpStatus"] <= 599)
            or type(request["streamCompleted"]) is not bool
            or (request["httpStatus"] == 200) != request["streamCompleted"]
        ):
            raise ValueError("complete response identity required; no inferred stream completion")
    if (
        len({r["requestId"] for r in requests}) != len(requests)
        or not requests[-1]["streamCompleted"]
    ):
        raise ValueError("unique response identities and a completed final stream required")
    return value
