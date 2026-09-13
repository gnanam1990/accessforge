"""Transactional ports model ordering/recovery, not actual database/provider/source acceptance."""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import ProposedChange
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.repair import delivery
from accessforge_orchestrator.repair.operator import main
from accessforge_orchestrator.repair.worker import RepairResult
from accessforge_persistence import (
    diagnosis_invocations,
    patches,
    repair_deliveries,
    repair_requests,
)


def test_operator_cost_flag_required_before_repository_or_database_access() -> None:
    ident = str(uuid4())
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "--workspace-id",
                ident,
                "--request-id",
                ident,
                "--project-id",
                ident,
                "--repository",
                "/missing-synthetic-repository",
            ]
        )
    assert stopped.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "post-scope", "write-failure", "lost-commit", "before-call"]
)
async def test_reserved_once_and_atomic_result_recovery(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    ws, request_id = str(uuid4()), str(uuid4())
    state: dict[str, Any] = {"status": "NOT_STARTED", "receipt": None, "patch": None}
    calls: list[str] = []
    decision = {
        "requestId": request_id,
        "findingId": "finding",
        "requestedBy": "actor",
        "scopeDigest": "a" * 64,
        "scope": {"separateReviewAcknowledged": False},
    }
    inputs = SimpleNamespace(
        model_dump=lambda **_: {"source": "synthetic"},
        base_manifest_digest="b" * 64,
        source=SimpleNamespace(tree_digest="c" * 64),
    )
    prepared = SimpleNamespace(inputs=inputs, run_id="run", binding_digest="d" * 64)
    changes = (ProposedChange("src/form.ts", "synthetic new text"),)
    transactions = 0

    @contextmanager
    def connection(*args: Any) -> Iterator[Any]:
        nonlocal transactions
        transactions += 1
        transaction = transactions
        saved = deepcopy(state)
        try:
            yield object()
        except BaseException:
            state.clear()
            state.update(saved)
            raise
        if fault == "lost-commit" and transaction == 2:
            raise RuntimeError("synthetic response lost after commit")

    def inspect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {**decision, "delivery": state["receipt"], "invocationState": state["status"]}

    preparations = 0

    def prepare(*args: Any, **kwargs: Any) -> Any:
        nonlocal preparations
        preparations += 1
        if fault == "post-scope" and preparations == 2:
            raise RuntimeError("scope changed after provider work")
        return prepared

    def reserve(*args: Any, **kwargs: Any) -> None:
        assert state["status"] == "NOT_STARTED" and kwargs["purpose"] == "REPAIR"
        state["status"] = "STARTED"
        calls.append("reserved")

    def finish(*args: Any, **kwargs: Any) -> None:
        if state["status"] != "STARTED":
            raise ValueError("cannot overwrite a final disposition")
        state["status"] = kwargs["status"]

    class Worker:
        def __init__(self, *, profile: Any, agent_builder: Any) -> None:
            self.profile = profile

        async def propose(
            self, inputs: Any, *, cancel_signal: Event, on_provider_invoke: Any
        ) -> RepairResult:
            assert state["status"] == "STARTED" and transactions == 1
            if fault == "before-call":
                raise RuntimeError("local construction failed before provider")
            calls.append("model")
            on_provider_invoke()
            return RepairResult(
                "PROPOSAL_READY",
                digest(
                    {
                        "repairInput": inputs.model_dump(),
                        "modelProfile": self.profile.model_dump(mode="json"),
                    }
                ),
                changes,
                patches.patch_digest(changes),
                rationale="Synthetic",
                uncertainty="Unverified",
            )

    def proposal(*args: Any, **kwargs: Any) -> Any:
        state["patch"] = SimpleNamespace(patch_id="patch-1")
        assert kwargs["changes"] == changes and kwargs["proposed_by"] == "actor"
        return state["patch"]

    def record(*args: Any, **kwargs: Any) -> None:
        assert state["patch"] is not None and state["status"] == "STARTED"
        state["receipt"] = {"requestId": request_id, "patchId": "patch-1", "outcome": "PROPOSED"}
        if fault == "write-failure":
            raise RuntimeError("synthetic receipt transaction failure")

    monkeypatch.setattr(delivery, "workspace_connection", connection)
    monkeypatch.setattr(delivery, "_prepared", prepare)
    monkeypatch.setattr(delivery, "RepairWorker", Worker)
    monkeypatch.setattr(repair_requests, "inspect", inspect)
    monkeypatch.setattr(repair_requests, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(diagnosis_invocations, "reserve", reserve)
    monkeypatch.setattr(diagnosis_invocations, "finish", finish)
    monkeypatch.setattr(patches, "propose_patch", proposal)
    monkeypatch.setattr(repair_deliveries, "record", record)
    monkeypatch.setattr(repair_deliveries, "by_request", lambda *args, **kwargs: state["receipt"])
    args: dict[str, Any] = {"workspace_id": ws, "request_id": request_id, "repositories": {}}
    store: Any = object()
    if fault is not None:
        with pytest.raises(RuntimeError):
            await delivery.deliver_requested("synthetic-db", store, **args)
    else:
        assert (await delivery.deliver_requested("synthetic-db", store, **args))[
            "patchId"
        ] == "patch-1"
    if fault in {None, "lost-commit"}:
        assert state["status"] == "RECORDED" and state["patch"] is not None
        assert (await delivery.deliver_requested("synthetic-db", store, **args))[
            "patchId"
        ] == "patch-1"
    else:
        assert state["status"] == ("NOT_CALLED" if fault == "before-call" else "UNCONFIRMED")
        assert state["patch"] is None and state["receipt"] is None
        with pytest.raises(Refused):
            await delivery.deliver_requested("synthetic-db", store, **args)
    assert calls == (["reserved"] if fault == "before-call" else ["reserved", "model"])
