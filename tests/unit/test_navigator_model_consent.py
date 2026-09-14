"""Pure provider-profile and conservative reservation bounds; never provider access."""

from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.navigator_model import default_profile, reserved_tokens, validate_profile
from accessforge_orchestrator.navigator.config import NavigatorModelProfile


def test_consent_profile_matches_the_actual_adapter_and_reserves_each_attempt() -> None:
    assert default_profile() == NavigatorModelProfile().model_dump(mode="json")
    assert reserved_tokens(default_profile()) == 24000
    assert (
        reserved_tokens(
            {**default_profile(), "model_attempts": 3, "invocation_total_tokens": 50000}
        )
        == 150000
    )


@pytest.mark.parametrize(
    "change",
    [
        {"aws_secret_access_key": "not permitted"},
        {"model_attempts": True},
        {"call_timeout_seconds": float("nan")},
        {"call_timeout_seconds": 0.5},
        {"call_timeout_seconds": 1.5},
        {"call_timeout_seconds": 10**1000},
        {"provider": "other"},
        {"retry_initial_delay_seconds": 5, "retry_max_delay_seconds": 1},
    ],
)
def test_no_hidden_provider_configuration_or_unbounded_budget(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_profile({**default_profile(), **change})


@pytest.mark.parametrize("seconds", [1, 30, 120, 1.0, 30.0, 120.0])
def test_every_supported_timeout_has_the_same_consent_and_adapter_digest(seconds: float) -> None:
    configured = {**default_profile(), "call_timeout_seconds": seconds}
    validate_profile(configured)
    adapter = NavigatorModelProfile.model_validate(configured)
    assert digest(configured) == digest(adapter.model_dump(mode="json"))


@pytest.mark.parametrize("seconds", [0.5, 1.5, 119.9, True, float("inf")])
def test_adapter_cannot_admit_an_unsealable_timeout(seconds: Any) -> None:
    with pytest.raises(ValueError):
        validate_profile({**default_profile(), "call_timeout_seconds": seconds})
    if seconds is not True:  # Pydantic coercion is separate from the strict HTTP profile boundary.
        with pytest.raises(ValueError):
            NavigatorModelProfile(call_timeout_seconds=seconds)
