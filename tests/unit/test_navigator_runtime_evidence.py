"""Original model receipts bind every action, never only a favorable subset of turns."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.codex_navigation import MEANING as CODEX_MEANING
from accessforge_domain.codex_navigation import default_profile as codex_profile
from accessforge_domain.navigator_model import default_profile
from accessforge_domain.navigator_runtime import MEANING
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.runtime_evidence import observed_model


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "missing",
        "unknown",
        "missing-action",
        "digest",
        "partial",
        "context",
        "distinct-completions",
        "reused-completion",
    ],
)
@pytest.mark.parametrize("codex", [False, True])
def test_original_runtime_receipt_is_required_for_each_action(case: str, codex: bool) -> None:
    context = {"run_id": "run", "attempt_id": "attempt", "manifest_digest": "seal"}
    profile = codex_profile() if codex else default_profile()
    observation: dict[str, Any] = {
        "meaning": MEANING,
        "profile": default_profile(),
        "requests": [{"requestId": "synthetic-1", "httpStatus": 200, "streamCompleted": True}],
    }
    if codex:
        observation = {
            "meaning": CODEX_MEANING,
            "profile": profile,
            "cli": {
                "threadId": str(UUID(int=1)),
                "version": profile["sdk_version"],
                "authMode": "chatgpt",
                "requestedModel": profile["model_id"],
                "exitCode": 0,
                "turnCompleted": True,
                "inputTokens": 10,
                "outputTokens": 5,
            },
        }
    turn: dict[str, Any] = {
        "operationId": "op",
        "actionSequence": 0,
        "resolvedActionId": "action",
        "status": "RECORDED",
        "modelConfigDigest": digest(profile),
        "observation": observation,
        "observationDigest": digest(observation),
    }
    snapshots: dict[str, Any] = {
        "MODEL_RUNTIME": {
            "format": "accessforge.model-runtime.v1",
            "runId": "run",
            "attemptId": "attempt",
            "manifestDigest": "seal",
            "turns": [turn],
        },
        "ACTION_TRACE": {
            "records": [
                {
                    "eventType": "ACTION_INTENT",
                    "payload": {"sourceRecord": {"sequence": 1, "actionId": "action"}},
                }
            ]
        },
    }
    if case == "missing":
        del snapshots["MODEL_RUNTIME"]
    elif case == "unknown":
        turn["status"] = "UNCONFIRMED"
    elif case == "missing-action":
        snapshots["ACTION_TRACE"]["records"] = []
    elif case == "digest":
        turn["observationDigest"] = "wrong"
    elif case == "partial":
        if codex:
            observation["cli"]["turnCompleted"] = False
        else:
            observation["requests"][0]["streamCompleted"] = False
    elif case == "context":
        snapshots["MODEL_RUNTIME"]["attemptId"] = "other-attempt"
    elif case in {"distinct-completions", "reused-completion"}:
        from copy import deepcopy

        second = deepcopy(turn)
        second.update(operationId="op-2", actionSequence=1, resolvedActionId="action-2")
        if case == "distinct-completions":
            if codex:
                second["observation"]["cli"]["threadId"] = str(UUID(int=2))
            else:
                second["observation"]["requests"][0]["requestId"] = "synthetic-2"
        second["observationDigest"] = digest(second["observation"])
        snapshots["MODEL_RUNTIME"]["turns"].append(second)
        snapshots["ACTION_TRACE"]["records"].append(
            {
                "eventType": "ACTION_INTENT",
                "payload": {"sourceRecord": {"sequence": 2, "actionId": "action-2"}},
            }
        )
    if case in {"digest", "partial", "context"} or (codex and case == "reused-completion"):
        with pytest.raises(Refused):
            observed_model(snapshots, context)
    else:
        assert observed_model(snapshots, context) == (
            digest(profile)
            if case in {"complete", "distinct-completions", "reused-completion"}
            else None
        )
