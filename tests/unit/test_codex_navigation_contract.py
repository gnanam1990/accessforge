"""Pure Codex/legacy receipt separation, not actual provider or reader execution."""

from copy import deepcopy
from typing import Any

import pytest

from accessforge_domain import codex_navigation, navigator_model, navigator_runtime
from accessforge_orchestrator.navigator.codex import CodexNavigationProfile


def receipt() -> dict[str, Any]:
    return {
        "meaning": codex_navigation.MEANING,
        "profile": codex_navigation.default_profile(),
        "cli": {
            "threadId": "00000000-0000-4000-8000-000000000001",
            "version": "0.154.0",
            "authMode": "chatgpt",
            "requestedModel": "gpt-6-astra",
            "exitCode": 0,
            "turnCompleted": True,
            "inputTokens": 10,
            "outputTokens": 5,
        },
    }


def test_profile_matches_planner_and_uses_explicit_admission_hold() -> None:
    profile = codex_navigation.default_profile()
    assert profile == CodexNavigationProfile().model_dump(mode="json")
    navigator_model.validate_profile(profile)
    assert navigator_model.reserved_tokens(profile) == 12000
    assert navigator_runtime.validate_observation(receipt()) == receipt()


@pytest.mark.parametrize(
    "key,value",
    [
        ("exitCode", False),
        ("turnCompleted", False),
        ("authMode", "apiKey"),
        ("requestedModel", "wrong"),
        ("version", "0.0.0"),
        ("inputTokens", True),
        ("outputTokens", 50000),
        ("threadId", "not-a-thread"),
        ("httpStatus", 200),
    ],
)
def test_incomplete_or_mislabelled_cli_observation_rejected(key: str, value: Any) -> None:
    original = receipt()
    changed = deepcopy(original)
    changed["cli"][key] = value
    with pytest.raises(ValueError):
        navigator_runtime.validate_observation(changed)
    assert navigator_runtime.validate_observation(original) == original


def test_legacy_receipt_cannot_be_relabelled_with_codex_profile() -> None:
    with pytest.raises(ValueError, match="original Bedrock"):
        navigator_runtime.validate_observation(
            {
                "meaning": navigator_runtime.MEANING,
                "profile": codex_navigation.default_profile(),
                "requests": [{"requestId": "made-up", "httpStatus": 200, "streamCompleted": True}],
            }
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("provider", "amazon-bedrock"),
        ("call_timeout_seconds", 0.5),
        ("invocation_total_tokens", True),
        ("budget_semantics", "HARD_SPEND_CAP"),
        ("model_attempts", 1),
    ],
)
def test_closed_codex_profile(key: str, value: Any) -> None:
    profile = codex_navigation.default_profile()
    profile[key] = value
    with pytest.raises(ValueError):
        navigator_model.validate_profile(profile)
