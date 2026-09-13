"""Pure provider-profile and conservative reservation bounds; never provider access."""

from typing import Any

import pytest

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
        {"provider": "other"},
        {"retry_initial_delay_seconds": 5, "retry_max_delay_seconds": 1},
    ],
)
def test_no_hidden_provider_configuration_or_unbounded_budget(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_profile({**default_profile(), **change})
