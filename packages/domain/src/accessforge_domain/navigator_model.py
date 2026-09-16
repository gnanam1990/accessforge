"""Pure, closed navigator provider profile shared by consent and the Strands adapter."""

from __future__ import annotations

import math
from typing import Any

from . import codex_navigation

IDENTITY = {
    "sdk_distribution": "strands-agents",
    "sdk_version": "1.55.1",
    "provider": "amazon-bedrock",
    "model_id": "global.anthropic.claude-sonnet-4-6",
    "region_name": "us-east-1",
}
INTEGER_BOUNDS = {
    "temperature": (0, 0),
    "provider_max_tokens": (64, 4096),
    "invocation_turns": (1, 1),
    "invocation_output_tokens": (64, 4096),
    "invocation_total_tokens": (512, 50000),
    "max_context_characters": (1000, 100000),
    "model_attempts": (1, 3),
    "retry_initial_delay_seconds": (1, 5),
    "retry_max_delay_seconds": (1, 10),
}


def default_profile() -> dict[str, Any]:
    return {
        **IDENTITY,
        "temperature": 0,
        "provider_max_tokens": 512,
        "invocation_turns": 1,
        "invocation_output_tokens": 1024,
        "invocation_total_tokens": 12000,
        "max_context_characters": 24000,
        "call_timeout_seconds": 30,
        "model_attempts": 2,
        "retry_initial_delay_seconds": 1,
        "retry_max_delay_seconds": 2,
    }


def validate_profile(profile: dict[str, Any]) -> None:
    if profile.get("provider") == "codex-chatgpt":
        codex_navigation.validate_profile(profile)
        return
    if set(profile) != set(IDENTITY) | set(INTEGER_BOUNDS) | {"call_timeout_seconds"}:
        raise ValueError("complete closed navigator model profile required")
    if any(profile[key] != value for key, value in IDENTITY.items()):
        raise ValueError("reviewed navigator provider identity required")
    if any(
        type(profile[key]) is not int or not low <= profile[key] <= high
        for key, (low, high) in INTEGER_BOUNDS.items()
    ):
        raise ValueError("finite navigator integer budgets required")
    timeout = profile["call_timeout_seconds"]
    # This field participates in the sealed configuration digest. Canonical authority forbids
    # fractional floats; integral floats remain equivalent to their integer JSON representation.
    if (
        type(timeout) not in (int, float)
        or not 1 <= timeout <= 120
        or not math.isfinite(timeout)
        or int(timeout) != timeout
    ):
        raise ValueError("navigator call timeout must be 1 to 120 whole seconds")
    if profile["retry_max_delay_seconds"] < profile["retry_initial_delay_seconds"]:
        raise ValueError("navigator retry delays are inverted")


def reserved_tokens(profile: dict[str, Any]) -> int:
    """Conservative hold includes all configured provider attempts; not measured use or money."""
    validate_profile(profile)
    if profile["provider"] == "codex-chatgpt":
        # A quota admission hold only; CLI-internal HTTP retries are not observed or capped here.
        return int(profile["invocation_total_tokens"])
    return int(profile["invocation_total_tokens"] * profile["model_attempts"])
