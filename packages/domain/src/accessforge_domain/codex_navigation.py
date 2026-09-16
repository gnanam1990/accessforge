"""Codex navigation identity and local CLI observations, never Bedrock HTTP attestations."""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

MEANING = "CLI_CONFIGURATION_AND_COMPLETION_NOT_PROVIDER_MODEL_ATTESTATION"
IDENTITY = {
    "provider": "codex-chatgpt",
    "sdk_version": "0.154.0",
    "model_id": "gpt-6-astra",
    "budget_semantics": "RESULT_ADMISSION_NOT_SPEND_CAP",
}
BOUNDS = {
    "invocation_output_tokens": (256, 4096),
    "invocation_total_tokens": (1000, 50000),
    "max_context_characters": (1000, 100000),
}


def default_profile() -> dict[str, Any]:
    return {
        **IDENTITY,
        "invocation_output_tokens": 1024,
        "invocation_total_tokens": 12000,
        "max_context_characters": 24000,
        "call_timeout_seconds": 30,
    }


def validate_profile(profile: dict[str, Any]) -> None:
    if set(profile) != set(IDENTITY) | set(BOUNDS) | {"call_timeout_seconds"}:
        raise ValueError("complete closed Codex navigation profile required")
    if any(profile[key] != value for key, value in IDENTITY.items()):
        raise ValueError("reviewed Codex navigation identity required")
    if any(
        type(profile[key]) is not int or not low <= profile[key] <= high
        for key, (low, high) in BOUNDS.items()
    ):
        raise ValueError("bounded Codex result-admission limits required")
    timeout = profile["call_timeout_seconds"]
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 1 <= timeout <= 120
        or int(timeout) != timeout
        or profile["invocation_output_tokens"] > profile["invocation_total_tokens"]
    ):
        raise ValueError("whole-second timeout and consistent Codex token envelope required")


def validate_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"meaning", "profile", "cli"}:
        raise ValueError("closed Codex runtime observation required")
    if value["meaning"] != MEANING or not isinstance(value["profile"], dict):
        raise ValueError("Codex runtime meaning and profile required")
    validate_profile(value["profile"])
    cli = value["cli"]
    expected = {
        "threadId",
        "version",
        "authMode",
        "requestedModel",
        "exitCode",
        "turnCompleted",
        "inputTokens",
        "outputTokens",
    }
    if not isinstance(cli, dict) or set(cli) != expected:
        raise ValueError("closed original CLI completion required")
    try:
        if not isinstance(cli["threadId"], str) or str(UUID(cli["threadId"])) != cli["threadId"]:
            raise ValueError("canonical Codex thread identity required")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("canonical Codex thread identity required") from exc
    if (
        cli["version"] != value["profile"]["sdk_version"]
        or cli["requestedModel"] != value["profile"]["model_id"]
        or cli["authMode"] != "chatgpt"
        or cli["turnCompleted"] is not True
        or type(cli["exitCode"]) is not int
        or cli["exitCode"] != 0
    ):
        raise ValueError("settled original Codex OAuth CLI execution required")
    incoming, outgoing = cli["inputTokens"], cli["outputTokens"]
    if (
        type(incoming) is not int
        or type(outgoing) is not int
        or min(incoming, outgoing) < 0
        or outgoing > value["profile"]["invocation_output_tokens"]
        or incoming + outgoing > value["profile"]["invocation_total_tokens"]
    ):
        raise ValueError("observed Codex usage exceeds result-admission limits")
    return value
