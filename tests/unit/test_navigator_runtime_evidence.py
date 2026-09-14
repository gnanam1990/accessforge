"""Original model receipts bind every action, never only a favorable subset of turns."""

from __future__ import annotations

from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.navigator_model import default_profile
from accessforge_domain.navigator_runtime import MEANING
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.runtime_evidence import observed_model


@pytest.mark.parametrize(
    "case", ["complete", "missing", "unknown", "missing-action", "digest", "partial", "context"]
)
def test_original_runtime_receipt_is_required_for_each_action(case: str) -> None:
    context = {"run_id": "run", "attempt_id": "attempt", "manifest_digest": "seal"}
    observation = {
        "meaning": MEANING,
        "profile": default_profile(),
        "requests": [{"requestId": "synthetic-1", "httpStatus": 200, "streamCompleted": True}],
    }
    turn = {
        "operationId": "op",
        "actionSequence": 0,
        "resolvedActionId": "action",
        "status": "RECORDED",
        "modelConfigDigest": digest(default_profile()),
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
        snapshots["MODEL_RUNTIME"]["turns"][0]["observation"]["requests"][0]["streamCompleted"] = (
            False
        )
    elif case == "context":
        snapshots["MODEL_RUNTIME"]["attemptId"] = "other-attempt"
    if case in {"digest", "partial", "context"}:
        with pytest.raises(Refused):
            observed_model(snapshots, context)
    else:
        assert observed_model(snapshots, context) == (
            digest(default_profile()) if case == "complete" else None
        )
