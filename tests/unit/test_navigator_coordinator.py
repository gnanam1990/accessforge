"""Coordinator lifecycle with explicit synthetic boundaries; no provider, DB or native AT calls."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from threading import Event
from types import SimpleNamespace
from typing import Any, cast

import pytest
from strands.agent.agent_result import AgentResult
from strands.types.agent import Limits

from accessforge_domain.canonical import digest
from accessforge_navigation_tools import (
    DispatchResult,
    NavigatorProjection,
    ReaderObservation,
    SupervisorDispatchRequest,
    ToolRefusal,
)
from accessforge_orchestrator.manual_dispatch import DispatchReference
from accessforge_orchestrator.navigator import coordinator
from accessforge_orchestrator.navigator.checkpoints import CheckpointKind, PlanningCheckpoint
from accessforge_orchestrator.navigator.config import NavigatorModelProfile
from accessforge_orchestrator.navigator.projection import RetainedNavigatorTurn
from accessforge_orchestrator.navigator.tooling import make_navigation_tool
from accessforge_persistence import navigator_model_calls

REF = DispatchReference(
    workspace_id="00000000-0000-0000-0000-000000000001",
    run_id="00000000-0000-0000-0000-000000000002",
    attempt_id="00000000-0000-0000-0000-000000000003",
    runner_id="00000000-0000-0000-0000-000000000004",
    lease_id="00000000-0000-0000-0000-000000000005",
    epoch=1,
)
RUN_REF = "navigator:" + digest(asdict(REF))
NOW = "2026-09-13T12:00:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_codex_coordinator_preserves_reservation_authority_and_retention(
    harness: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    revoked: bool,
) -> None:
    from pydantic import BaseModel

    from accessforge_domain.codex_navigation import MEANING
    from accessforge_navigation_tools import ProposedAction
    from accessforge_orchestrator.codex_agent import CodexStructuredAgent, StructuredResult
    from accessforge_orchestrator.navigator import codex
    from accessforge_persistence import navigator_runtime

    h = harness
    h.revoked = revoked
    profile = codex.CodexNavigationProfile()
    h.model_digest = digest(profile.model_dump(mode="json"))
    h.session._profile = profile

    class Model(CodexStructuredAgent):
        async def invoke_async(
            self,
            prompt: str,
            *,
            structured_output_model: type[BaseModel],
            limits: Limits,
            cancel_signal: Event,
        ) -> StructuredResult:
            h.events.append("codex-provider")
            return StructuredResult(
                ProposedAction.model_validate(
                    {
                        "runRef": RUN_REF,
                        "action": "STOP",
                    }
                ),
                {
                    "threadId": "00000000-0000-4000-8000-000000000001",
                    "version": "0.154.0",
                    "authMode": "chatgpt",
                    "requestedModel": "gpt-6-astra",
                    "exitCode": 0,
                    "turnCompleted": True,
                    "inputTokens": 10,
                    "outputTokens": 5,
                },
            )

    def retain(conn: Any, **kwargs: Any) -> None:
        h.events.append(("runtime-retained", kwargs["observation"]))

    monkeypatch.setattr(codex, "CodexStructuredAgent", Model)
    monkeypatch.setattr(navigator_runtime, "retain", retain)
    result = await h.session.run_next_turn()
    if revoked:
        assert result.disposition == "NOT_CALLED"
        assert "codex-provider" not in h.events
    else:
        assert result.disposition == "RECORDED" and result.next_action_sequence is None
        first_reserve = next(
            i for i, e in enumerate(h.events) if isinstance(e, tuple) and e[0] == "reserve"
        )
        assert h.events[first_reserve + 1] == "committed"
        assert first_reserve < h.events.index("authorize") < h.events.index("codex-provider")
        retained = next(
            e[1] for e in h.events if isinstance(e, tuple) and e[0] == "runtime-retained"
        )
        assert retained["meaning"] == MEANING and "requests" not in retained
        assert retained["profile"] == profile.model_dump(mode="json")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    h = SimpleNamespace(
        events=[],
        action="NEXT",
        mode="normal",
        started=asyncio.Event(),
        checkpoint_failure=False,
        finish_failure=False,
        revoked=False,
        model_digest=digest(NavigatorModelProfile().model_dump(mode="json")),
    )

    @contextmanager
    def connection(*args: Any) -> Iterator[object]:
        del args
        h.events.append("transaction-open")
        yield object()
        h.events.append("committed")

    def reserve(conn: object, **kwargs: Any) -> str:
        del conn
        h.events.append(("reserve", kwargs))
        return digest({"request": kwargs["operation_id"]})

    def authorize(conn: object, **kwargs: Any) -> None:
        del conn, kwargs
        h.events.append("authorize")
        if h.revoked:
            raise ToolRefusal("consent revoked")

    def finish(conn: object, **kwargs: Any) -> None:
        del conn
        h.events.append(("finish", kwargs["status"]))
        if h.finish_failure:
            raise RuntimeError("unknown accounting commit")

    def load(**kwargs: Any) -> RetainedNavigatorTurn:
        sequence = kwargs["expected_action_sequence"]
        observations = [
            ReaderObservation(
                phrase="Synthetic reader phrase",
                capturedAtUtc=NOW,
                actionId=f"00000000-0000-0000-0000-{index:012d}",
                actionSequence=index,
                provenance="ACTUAL_READER",
            )
            for index in range(1, sequence + 1)
        ]
        projection = NavigatorProjection.from_policy(
            run_ref=RUN_REF,
            policy={
                "taskSummary": "Synthetic navigation fixture",
                "successCondition": "Explicit supervisor evidence required",
                "startUrl": "https://app.example.test/form",
                "allowedActions": ["NEXT", "STOP", "TYPE_TEXT"],
                "allowedKeyChords": [],
                "maxActions": 10,
                "wallTimeSeconds": 120,
                "fixtureValues": {"fullName": "Synthetic Person"},
                "forbiddenObservations": [
                    "DOM",
                    "SELECTORS",
                    "SCREENSHOTS",
                    "SOURCE",
                    "OBSERVER_RECEIPTS",
                    "ASSERTION_EXPECTATIONS",
                ],
            },
            reader_observations=observations,
        )
        return RetainedNavigatorTurn(
            projection,
            "a" * 64,
            h.model_digest,
            sequence,
            tuple(item.action_id for item in observations),
            tuple(digest(item.model_dump(mode="json")) for item in observations),
        )

    class Transport:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            h.events.append("transport-created")

        def close(self) -> None:
            h.events.append("transport-closed")

        async def __call__(self, request: SupervisorDispatchRequest) -> DispatchResult:
            h.events.append(("native-dispatch", request))
            if h.mode == "native-unknown":
                raise RuntimeError("synthetic lost reply")
            return DispatchResult("SUCCEEDED", "00000000-0000-0000-0000-000000000099")

    class Sink:
        def __init__(self, **kwargs: Any) -> None:
            h.events.append(("sink-bound", kwargs))

        async def retain(self, checkpoint: PlanningCheckpoint) -> None:
            h.events.append(("checkpoint", checkpoint))
            if h.checkpoint_failure:
                raise RuntimeError("checkpoint unavailable")

    def build(**kwargs: Any) -> Any:
        h.events.append("provider-construction")
        if h.mode == "construction-error":
            raise RuntimeError("credential construction uncertain")
        tool = make_navigation_tool(**kwargs)

        class Agent:
            async def invoke_async(
                self, prompt: str, *, limits: Limits, cancel_signal: Event
            ) -> AgentResult:
                del limits, cancel_signal
                assert "private-native-token" not in prompt
                assert "operation_id" not in prompt
                h.events.append("provider-entered")
                if h.mode == "provider-error":
                    raise RuntimeError("synthetic provider error")
                if h.mode == "cancel":
                    h.started.set()
                    await asyncio.Event().wait()
                if h.mode != "no-action":
                    proposal = {"run_ref": RUN_REF, "action": h.action}
                    if h.action == "TYPE_TEXT":
                        proposal["text_value_ref"] = "fullName"
                    await tool.submit(proposal)
                return cast(AgentResult, SimpleNamespace(stop_reason="end_turn"))

        return Agent()

    def build_adapter(**kwargs: Any) -> Any:
        kwargs.pop("profile")
        return build(**kwargs)

    monkeypatch.setattr(coordinator, "workspace_connection", connection)
    monkeypatch.setattr(coordinator, "load_retained_turn", load)
    monkeypatch.setattr(navigator_model_calls, "reserve_turn", reserve)
    monkeypatch.setattr(navigator_model_calls, "assert_turn_authorized", authorize)
    monkeypatch.setattr(navigator_model_calls, "finish_turn", finish)
    monkeypatch.setattr(coordinator, "NativeNavigatorTransport", Transport)
    monkeypatch.setattr(coordinator, "PostgresPlanningCheckpointSink", Sink)
    monkeypatch.setattr(coordinator, "build_strands_agent", build_adapter)
    h.session = coordinator.NativeNavigatorSession(
        database_url="unused",
        reference=REF,
        consent_id="synthetic-consent",
        profile=NavigatorModelProfile(),
        private_reference={"token": "private-native-token"},
    )
    return h


@pytest.mark.asyncio
async def test_commit_then_checkpoint_then_provider_and_only_native_fixture_reference(
    harness: SimpleNamespace,
) -> None:
    h = harness
    h.action = "TYPE_TEXT"
    first = await h.session.run_next_turn()
    assert first.disposition == "RECORDED" and first.next_action_sequence == 1
    reserve_index = next(
        i for i, e in enumerate(h.events) if isinstance(e, tuple) and e[0] == "reserve"
    )
    assert h.events[reserve_index + 1] == "committed"
    assert reserve_index < h.events.index("provider-construction")
    retained = [e[1] for e in h.events if isinstance(e, tuple) and e[0] == "checkpoint"]
    assert retained[0].kind is CheckpointKind.MODEL_CALL_STARTED
    dispatch = next(e[1] for e in h.events if isinstance(e, tuple) and e[0] == "native-dispatch")
    assert dispatch.text is None and dispatch.text_value_ref == "fullName"
    binding = next(e[1] for e in h.events if isinstance(e, tuple) and e[0] == "sink-bound")
    assert binding["operation_id"] == first.operation_id
    h.action = "STOP"
    second = await h.session.run_next_turn()
    assert second.disposition == "RECORDED" and second.next_action_sequence is None
    assert first.operation_id != second.operation_id
    assert h.events.count("transport-created") == 1
    with pytest.raises(ToolRefusal):
        await h.session.run_next_turn()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["construction-error", "provider-error", "native-unknown"])
async def test_uncertain_provider_or_native_effect_keeps_hold_and_fences(
    harness: SimpleNamespace,
    mode: str,
) -> None:
    harness.mode = mode
    result = await harness.session.run_next_turn()
    assert result.disposition == "UNCONFIRMED" and result.next_action_sequence is None
    assert ("finish", "UNCONFIRMED") in harness.events
    with pytest.raises(ToolRefusal):
        await harness.session.run_next_turn()


@pytest.mark.asyncio
async def test_preconstruction_revocation_never_enters_provider(harness: SimpleNamespace) -> None:
    harness.revoked = True
    result = await harness.session.run_next_turn()
    assert result.disposition == "NOT_CALLED" and result.next_action_sequence is None
    assert "provider-construction" not in harness.events


@pytest.mark.asyncio
async def test_checkpoint_failure_before_construction_is_provably_not_called(
    harness: SimpleNamespace,
) -> None:
    harness.checkpoint_failure = True
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await harness.session.run_next_turn()
    assert ("finish", "NOT_CALLED") in harness.events
    assert "provider-construction" not in harness.events


@pytest.mark.asyncio
async def test_cancelled_task_after_entry_is_consumed_not_replayed(
    harness: SimpleNamespace,
) -> None:
    harness.mode = "cancel"
    task = asyncio.create_task(harness.session.run_next_turn())
    await asyncio.wait_for(harness.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("finish", "UNCONFIRMED") in harness.events
    with pytest.raises(ToolRefusal):
        await harness.session.run_next_turn()


@pytest.mark.asyncio
async def test_unknown_final_accounting_cannot_return_a_continuation(
    harness: SimpleNamespace,
) -> None:
    harness.finish_failure = True
    with pytest.raises(RuntimeError, match="unknown accounting commit"):
        await harness.session.run_next_turn()
    with pytest.raises(ToolRefusal):
        await harness.session.run_next_turn()


@pytest.mark.asyncio
async def test_returned_provider_without_an_action_cannot_spin_another_call(
    harness: SimpleNamespace,
) -> None:
    harness.mode = "no-action"
    result = await harness.session.run_next_turn()
    assert result.disposition == "RECORDED" and result.next_action_sequence is None
    with pytest.raises(ToolRefusal):
        await harness.session.run_next_turn()
