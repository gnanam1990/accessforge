"""Explicit host protocol and serial invocation ownership; no provider, DB or AT use."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from accessforge_domain.navigator_model import default_profile
from accessforge_orchestrator.navigator import operator
from accessforge_orchestrator.navigator.agent import NavigatorInvocationResult, NavigatorStopReason
from accessforge_orchestrator.navigator.coordinator import AdmittedTurnResult


def envelope() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "reference": {
            **{
                key: str(uuid4())
                for key in (
                    "workspaceId",
                    "runId",
                    "attemptId",
                    "runnerId",
                    "leaseId",
                )
            },
            "epoch": 1,
        },
        "consentId": str(uuid4()),
        "modelProfile": default_profile(),
        "privateReference": {"token": "synthetic-private-token"},
    }


def test_closed_host_input_accepts_only_explicit_canonical_identity_and_profile() -> None:
    config = envelope()
    assert operator.parse_host_input(json.dumps(config).encode()) == config
    for changed in (
        {**config, "schemaVersion": True},
        {**config, "consentId": "not-a-uuid"},
        {**config, "reference": {**config["reference"], "epoch": True}},
        {**config, "reference": {**config["reference"], "url": "untrusted"}},
        {**config, "modelProfile": {**default_profile(), "aws_secret_access_key": "not-allowed"}},
        {**config, "environment": {"GITHUB_TOKEN": "not-allowed"}},
    ):
        with pytest.raises(ValueError):
            operator.parse_host_input(json.dumps(changed).encode())
    raw = json.dumps(config).encode()
    with pytest.raises(ValueError, match="duplicate"):
        operator.parse_host_input(raw[:-1] + b', "schemaVersion": 1}')
    with pytest.raises(ValueError):
        operator.parse_host_input(b"x" * (operator.MAX_INPUT + 1))


def test_operator_never_reads_private_fds_without_explicit_billable_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected() -> dict[str, Any]:
        raise AssertionError("private input must not be read")

    monkeypatch.setattr(operator, "read_host_input", unexpected)
    with pytest.raises(SystemExit) as caught:
        operator.main([])
    assert caught.value.code == 2
    assert "no provider work started" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", [True, False])
async def test_child_runs_serially_and_distinguishes_stop_from_other_terminal_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    stopped: bool,
) -> None:
    config = envelope()
    calls: list[str] = []
    ids = [str(uuid4()), str(uuid4())]

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["database_url"] == "synthetic-db"
            assert kwargs["reference"].run_id == config["reference"]["runId"]
            assert kwargs["consent_id"] == config["consentId"]
            self.count = 0

        def close(self) -> None:
            calls.append("close")

        async def run_next_turn(self) -> AdmittedTurnResult:
            calls.append("call")
            self.count += 1
            return AdmittedTurnResult(
                ids[self.count - 1],
                NavigatorInvocationResult(stop_reason=NavigatorStopReason.COMPLETED),
                "RECORDED",
                1 if self.count == 1 else None,
                self.count == 2 and stopped,
            )

    monkeypatch.setattr(operator, "NativeNavigatorSession", Session)
    result = await operator.run_host_session(config, "synthetic-db")
    assert result == {
        "schemaVersion": 1,
        "reference": config["reference"],
        "status": "STOP_ACKNOWLEDGED" if stopped else "NOT_FINISHED",
        "completedCalls": 2,
        "lastOperationId": ids[-1],
    }
    assert calls == ["call", "call", "close"]
    assert "synthetic-private-token" not in json.dumps(result)
