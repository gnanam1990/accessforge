"""Pure provider-profile and conservative reservation bounds; never provider access."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.navigator_model import default_profile, reserved_tokens, validate_profile
from accessforge_orchestrator.navigator.config import NavigatorModelProfile
from accessforge_persistence import execution_approvals, navigator_model_calls, projects


def test_codex_disclosure_does_not_claim_a_bounded_provider_retry_or_spend_cap() -> None:
    from accessforge_domain.codex_navigation import default_profile as codex_profile

    disclosure = navigator_model_calls._disclosure(codex_profile())
    assert "Codex ChatGPT OAuth" in disclosure
    assert "consumes account usage" in disclosure
    assert "CLI-internal retries are not measured or capped here" in disclosure
    assert "not measured usage or a currency spending cap" in disclosure
    legacy = navigator_model_calls._disclosure(default_profile())
    assert "configured retries may be billable" in legacy
    assert "Codex" not in legacy


@pytest.mark.parametrize("action_budget", [1, 499, 500, 501, 10000])
def test_review_scope_caps_model_calls_without_refusing_a_larger_execution_budget(
    monkeypatch: pytest.MonkeyPatch, action_budget: int
) -> None:
    """Scope must offer only call counts that issue_consent can accept; no provider access."""
    expires = datetime.now(UTC) + timedelta(minutes=5)
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        {"revision": 2, "manifest_digest": "a" * 64},
        {"expires_at": expires},
    ]
    monkeypatch.setattr(
        projects,
        "find_sealed_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(sealed_manifest_id="test-seal"),
    )
    monkeypatch.setattr(
        execution_approvals,
        "assert_authorized",
        lambda *_args, **_kwargs: {
            "modelConfigDigest": digest(default_profile()),
            "authorizationId": "test-approval",
            "actionBudget": action_budget,
            "expiresAt": expires.isoformat().replace("+00:00", "Z"),
        },
    )
    scope = navigator_model_calls.review_scope(
        conn, workspace_id="test-workspace", run_id="test-run"
    )
    assert scope["maximumCalls"] == min(action_budget, 500)
    assert scope["billableCallAcknowledged"] is False
    assert scope["modelProfile"] == default_profile()


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
