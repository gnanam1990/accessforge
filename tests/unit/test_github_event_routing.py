"""Authenticated local dispatch selection, not remote delivery or database acceptance."""

import hashlib
import hmac
import json
from typing import Any

import pytest

from accessforge_orchestrator import github_receiver as receiver
from accessforge_orchestrator.github_webhooks import Refused
from accessforge_persistence.github_webhooks import Receipt


@pytest.mark.parametrize(
    "action,removals,expected",
    [
        ("removed", True, "receive_repository_removal"),
        ("removed", False, "receive"),
        ("suspend", False, "receive_installation_suspension"),
        ("opened", False, "receive"),
    ],
)
@pytest.mark.parametrize("valid_signature", [True, False])
def test_dispatch_follows_authentication(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    removals: bool,
    expected: str,
    valid_signature: bool,
) -> None:
    config = receiver.ReceiverConfig(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        7,
        "unused",
        b"x" * 32,
    )
    payload: dict[str, Any] = {"action": action, "installation": {"id": 42, "app_id": 7}}
    if removals:
        payload["repositories_removed"] = [{"id": 13}]
    body = json.dumps(payload).encode()
    signature = "sha256=" + (
        hmac.new(config.webhook_secret, body, hashlib.sha256).hexdigest()
        if valid_signature
        else "0" * 64
    )
    calls: list[str] = []
    receipt = Receipt("original-body", True)

    def selected(actual: receiver.ReceiverConfig, **kwargs: Any) -> Receipt:
        assert actual is config
        assert kwargs["raw_body"] == body
        assert kwargs["signature"] == signature
        calls.append(expected)
        return receipt

    monkeypatch.setattr(receiver, expected, selected)
    args: dict[str, Any] = dict(
        raw_body=body, signature=signature, delivery_id="00000000-0000-4000-8000-000000000003"
    )
    if valid_signature:
        assert receiver.receive_event(config, **args) is receipt
        assert calls == [expected]
    else:
        with pytest.raises(Refused):
            receiver.receive_event(config, **args)
        assert calls == []
