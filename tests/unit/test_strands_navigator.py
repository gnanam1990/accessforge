"""Adversarial tests for the actual Strands construction, without provider calls."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from pydantic import ValidationError
from strands.agent.agent_result import AgentResult
from strands.types.agent import Limits

from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS
from accessforge_navigation_tools import (
    ActionName,
    DispatchResult,
    NavigationRuntimeState,
    NavigationToolGateway,
    NavigatorProjection,
    ReaderObservation,
    SealedNavigatorPolicy,
    ToolRefusal,
)
from accessforge_orchestrator.navigator import (
    CheckpointKind,
    NavigationActionTool,
    NavigatorModelProfile,
    NavigatorStopReason,
    PlanningCheckpoint,
    PostgresPlanningCheckpointSink,
    StrandsNavigator,
    build_strands_agent,
    installed_strands_version,
    make_navigation_tool,
)

NOW = "2026-09-12T12:00:00Z"
RUN_REF = "run-1:attempt-1:epoch-4"


def policy() -> dict[str, object]:
    return {
        "taskSummary": "Recover from the form error and submit one synthetic request.",
        "successCondition": "The application confirms one request was received.",
        "startUrl": "http://127.0.0.1:8081/form/fixture",
        "allowedActions": sorted(ALLOWED_ACTIONS),
        "allowedKeyChords": ["ENTER", "ESCAPE", "SHIFT+TAB", "TAB"],
        "maxActions": 40,
        "wallTimeSeconds": 180,
        "fixtureValues": {"fullName": "Test Person", "email": "invalid-email"},
        "forbiddenObservations": [
            "DOM",
            "SELECTORS",
            "SCREENSHOTS",
            "SOURCE",
            "OBSERVER_RECEIPTS",
            "ASSERTION_EXPECTATIONS",
        ],
    }


def projection(*, phrase: str = "Email address, invalid entry") -> NavigatorProjection:
    return NavigatorProjection.from_policy(
        run_ref=RUN_REF,
        policy=policy(),
        reader_observations=[
            ReaderObservation.model_validate(
                {
                    "phrase": phrase,
                    "captured_at_utc": NOW,
                    "action_id": "action-1",
                    "action_sequence": 1,
                    "provenance": "ACTUAL_READER",
                }
            )
        ],
    )


class RecordingSink:
    def __init__(self) -> None:
        self.items: list[PlanningCheckpoint] = []

    async def retain(self, checkpoint: PlanningCheckpoint) -> None:
        self.items.append(checkpoint)


def gateway(dispatched: list[Any]) -> NavigationToolGateway:
    runtime = NavigationRuntimeState(
        lease_id="lease-1",
        lease_epoch=4,
        current_epoch=4,
        deadline_monotonic=100,
        cancel_requested=False,
        action_in_flight=False,
        now_monotonic=10,
        now_utc=NOW,
        actions_used=0,
        wall_time_used_seconds=1,
        observed_origin="http://127.0.0.1:8081",
    )

    async def dispatch(request: object) -> DispatchResult:
        dispatched.append(request)
        return DispatchResult(status="SUCCEEDED", action_id="action-2")

    return NavigationToolGateway(
        run_ref=RUN_REF,
        platform="darwin",
        permitted_origins=frozenset({"http://127.0.0.1:8081"}),
        policy=SealedNavigatorPolicy.model_validate(policy()),
        state_provider=lambda: runtime,
        dispatch=dispatch,
    )


def profile(**overrides: object) -> NavigatorModelProfile:
    values: dict[str, object] = {
        "model_id": "global.anthropic.claude-sonnet-4-6",
        "region_name": "us-east-1",
    }
    values.update(overrides)
    return NavigatorModelProfile.model_validate(values)


def test_real_strands_agent_has_one_tool_and_never_loads_a_poisoned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "poison-imported"
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "evil.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sink = RecordingSink()
    agent = build_strands_agent(
        profile=profile(),
        gateway=gateway([]),
        checkpoints=sink,
        cancel_fence=Event(),
        utc_now=lambda: NOW,
    )

    assert installed_strands_version() == "1.55.1"
    assert agent.tool_names == ["submit_navigation_action"]
    assert agent.load_tools_from_directory is False
    assert agent._session_manager is None
    assert agent.memory_manager is None
    assert marker.exists() is False

    tool_spec = agent.tool_registry.get_all_tools_config()["submit_navigation_action"]
    schema = tool_spec["inputSchema"]["json"]
    properties = schema["properties"]
    assert set(properties) == {"run_ref", "action", "key_chord", "text_value_ref"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("seconds", [1, 30, 120])
def test_navigator_transport_has_no_hidden_retries_or_configured_endpoint_override(
    monkeypatch: pytest.MonkeyPatch, seconds: int
) -> None:
    # Dummy credentials permit construction only. No invoke/stream/network operation is called.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret-key")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "99")
    monkeypatch.setenv("AWS_RETRY_MODE", "adaptive")
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "https://unreviewed.example.test")
    agent = build_strands_agent(
        profile=profile(call_timeout_seconds=seconds),
        gateway=gateway([]),
        checkpoints=RecordingSink(),
        cancel_fence=Event(),
        utc_now=lambda: NOW,
    )
    from strands.models import BedrockModel

    assert isinstance(agent.model, BedrockModel)
    actual = agent.model.client.meta
    assert actual.config.retries == {"total_max_attempts": 1, "mode": "standard"}
    assert actual.config.connect_timeout == min(10, seconds)
    assert actual.config.read_timeout == seconds
    assert actual.endpoint_url == "https://bedrock-runtime.us-east-1.amazonaws.com"
    assert actual.region_name == "us-east-1"
    agent.model.client.close()


@pytest.mark.parametrize("case", ["complete", "partial", "over-budget", "wrong-model", "cancelled"])
def test_runtime_request_observation_requires_bounded_completed_responses(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from types import SimpleNamespace

    from strands.models import BedrockModel

    from accessforge_domain.navigator_runtime import validate_observation
    from accessforge_orchestrator.navigator.runtime import ObservedBedrockModel

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret-key")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    configured, fence = profile(model_attempts=1), Event()
    agent = build_strands_agent(
        profile=configured,
        gateway=gateway([]),
        checkpoints=RecordingSink(),
        cancel_fence=fence,
        utc_now=lambda: NOW,
    )
    model = agent.model
    assert isinstance(model, ObservedBedrockModel)
    model.arm(
        agent,
        limits={"turns": 1, "output_tokens": 1024, "total_tokens": 12000},
        timeout=30,
        context_limit=24000,
        fence=fence,
        expected=configured,
    )

    # The real wrapper surrounds a synthetic SDK worker. No HTTP/provider invocation occurs.
    def synthetic_worker(self: Any, callback: Any, *args: Any, **kwargs: Any) -> None:
        if case == "cancelled":
            fence.set()
        for index in range(2 if case == "over-budget" else 1):
            model._before_request(
                {
                    "modelId": "wrong" if case == "wrong-model" else configured.model_id,
                    "inferenceConfig": {"maxTokens": 512, "temperature": 0},
                },
                SimpleNamespace(name="ConverseStream"),
            )
            model._after_response(
                {"ResponseMetadata": {"RequestId": f"synthetic-{index}", "HTTPStatusCode": 200}}
            )
        callback({"contentBlockDelta": {"delta": {"text": "DO-NOT-RETAIN-THIS"}}})
        callback({"messageStop": {"stopReason": "end_turn"}})
        if case != "partial":
            callback({"metadata": {"usage": {"inputTokens": 12}}})
        callback()

    monkeypatch.setattr(BedrockModel, "_stream", synthetic_worker)
    try:
        if case in {"wrong-model", "over-budget", "cancelled"}:
            with pytest.raises(RuntimeError, match="bounded observed runtime"):
                model._stream(lambda event=None: None, [])
        else:
            model._stream(lambda event=None: None, [])
        observation = model.observation()
        if case == "complete":
            assert observation is not None
            validate_observation(observation)
            assert observation["profile"] == configured.model_dump(mode="json")
            assert "DO-NOT-RETAIN" not in repr(observation)
        else:
            assert observation is None
    finally:
        model.client.close()


@pytest.mark.asyncio
async def test_runtime_receipt_uses_real_sdk_events_with_stubbed_provider_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret-key")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    configured, sink = profile(), RecordingSink()
    fence = Event()
    agent = build_strands_agent(
        profile=configured,
        gateway=gateway([]),
        checkpoints=sink,
        cancel_fence=fence,
        utc_now=lambda: NOW,
    )
    from accessforge_orchestrator.navigator.runtime import ObservedBedrockModel

    assert isinstance(agent.model, ObservedBedrockModel)
    response: dict[str, Any] = {
        "ResponseMetadata": {"RequestId": "synthetic-sdk-response", "HTTPStatusCode": 200},
        "stream": {},
    }
    with Stubber(agent.model.client) as stub:
        stub.add_response("converse_stream", response)
        # Stubber validates the service's EventStream shape before the actual iterable is supplied.
        # The SDK and botocore hooks are real; transport and stream bytes are entirely synthetic.
        response["stream"] = [
            {"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Synthetic stop."}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {
                "metadata": {
                    "usage": {"inputTokens": 12, "outputTokens": 3, "totalTokens": 15},
                    "metrics": {"latencyMs": 1},
                }
            },
        ]
        result = await StrandsNavigator(
            profile=configured,
            checkpoints=sink,
            utc_now=lambda: NOW,
            agent_builder=lambda _: agent,
        ).run_turn(projection(), cancel_signal=fence)
        stub.assert_no_pending_responses()
    agent.model.client.close()
    assert result.stop_reason is NavigatorStopReason.COMPLETED
    assert result.runtime_observation is not None
    assert result.runtime_observation["requests"] == [
        {"requestId": "synthetic-sdk-response", "httpStatus": 200, "streamCompleted": True}
    ]
    assert "Synthetic stop." not in repr(result.runtime_observation)


@pytest.mark.asyncio
async def test_actual_strands_stream_path_rejects_unknown_model_fields_without_dispatch() -> None:
    dispatched: list[Any] = []
    navigation_tool = make_navigation_tool(
        gateway=gateway(dispatched),
        checkpoints=RecordingSink(),
        cancel_fence=Event(),
        utc_now=lambda: NOW,
    )
    events = [
        event
        async for event in navigation_tool.stream(
            {
                "toolUseId": "tool-use-1",
                "name": "submit_navigation_action",
                "input": {"run_ref": RUN_REF, "action": "NEXT", "shell": "id"},
            },
            {},
        )
    ]
    assert events[-1].tool_result["status"] == "error"
    assert "ValidationError" in events[-1].tool_result["content"][0]["text"]
    assert dispatched == []


@pytest.mark.asyncio
async def test_checkpoint_contract_refuses_reader_or_observer_payload_and_wrong_run_ref() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlanningCheckpoint.model_validate(
            {
                "run_ref": RUN_REF,
                "kind": "ACTION_PROPOSED",
                "recorded_at_utc": NOW,
                "action": "NEXT",
                "announcement": "secret reader content",
                "observer_receipt": "answer key",
            }
        )

    sink = PostgresPlanningCheckpointSink(
        database_url="postgresql://unused",
        workspace_id="00000000-0000-0000-0000-000000000001",
        run_id="00000000-0000-0000-0000-000000000002",
        attempt_id="00000000-0000-0000-0000-000000000003",
        expected_run_ref=RUN_REF,
    )
    checkpoint = PlanningCheckpoint(
        run_ref="wrong-run",
        kind=CheckpointKind.ACTION_PROPOSED,
        recorded_at_utc=NOW,
        action=ActionName.NEXT,
    )
    with pytest.raises(ValueError, match="expected"):
        await sink.retain(checkpoint)


@pytest.mark.asyncio
async def test_tool_checkpoints_metadata_but_never_reader_text_or_resolved_fixture_value() -> None:
    sink = RecordingSink()
    dispatched: list[Any] = []
    navigation_tool = make_navigation_tool(
        gateway=gateway(dispatched),
        checkpoints=sink,
        cancel_fence=Event(),
        utc_now=lambda: NOW,
    )
    result = await navigation_tool.submit(
        {"run_ref": RUN_REF, "action": ActionName.TYPE_TEXT, "text_value_ref": "fullName"}
    )

    assert result["status"] == "SUCCEEDED"
    assert dispatched[0].text == "Test Person"
    retained = "\n".join(item.model_dump_json() for item in sink.items)
    assert "fullName" in retained
    assert "Test Person" not in retained
    assert "Email address" not in retained
    assert [item.kind for item in sink.items] == [
        CheckpointKind.ACTION_PROPOSED,
        CheckpointKind.ACTION_RESOLVED,
    ]


@pytest.mark.asyncio
async def test_closed_invocation_fence_denies_a_late_tool_call() -> None:
    fence = Event()
    fence.set()
    dispatched: list[Any] = []
    navigation_tool = make_navigation_tool(
        gateway=gateway(dispatched),
        checkpoints=RecordingSink(),
        cancel_fence=fence,
        utc_now=lambda: NOW,
    )
    with pytest.raises(ToolRefusal, match="MODEL_CALL_CANCELLED"):
        await navigation_tool.submit({"run_ref": RUN_REF, "action": ActionName.NEXT})
    assert dispatched == []


class FailResolvedCheckpointSink(RecordingSink):
    async def retain(self, checkpoint: PlanningCheckpoint) -> None:
        if checkpoint.kind is CheckpointKind.ACTION_RESOLVED:
            raise OSError("database unavailable")
        await super().retain(checkpoint)


@pytest.mark.asyncio
async def test_post_dispatch_checkpoint_failure_is_ambiguous_and_fences_a_retry() -> None:
    fence = Event()
    dispatched: list[Any] = []
    navigation_tool = make_navigation_tool(
        gateway=gateway(dispatched),
        checkpoints=FailResolvedCheckpointSink(),
        cancel_fence=fence,
        utc_now=lambda: NOW,
    )
    result = await navigation_tool.submit({"run_ref": RUN_REF, "action": ActionName.NEXT})

    assert result["status"] == "AMBIGUOUS"
    assert len(dispatched) == 1
    assert fence.is_set()
    with pytest.raises(ToolRefusal, match="INVOCATION_ACTION_ALREADY_CLAIMED"):
        await navigation_tool.submit({"run_ref": RUN_REF, "action": ActionName.NEXT})
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_one_model_response_cannot_dispatch_two_actions_without_a_fresh_observation() -> None:
    dispatched: list[Any] = []
    navigation_tool = make_navigation_tool(
        gateway=gateway(dispatched),
        checkpoints=RecordingSink(),
        cancel_fence=Event(),
        utc_now=lambda: NOW,
    )
    first = await navigation_tool.submit({"run_ref": RUN_REF, "action": ActionName.NEXT})
    assert first["status"] == "SUCCEEDED"

    with pytest.raises(ToolRefusal, match="INVOCATION_ACTION_ALREADY_CLAIMED"):
        await navigation_tool.submit({"run_ref": RUN_REF, "action": ActionName.ACTIVATE})
    assert len(dispatched) == 1


class ResultAgent:
    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason
        self.prompts: list[str] = []
        self.limits: list[Limits] = []

    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: Event,
    ) -> AgentResult:
        self.prompts.append(prompt)
        self.limits.append(limits)
        return cast(AgentResult, type("Result", (), {"stop_reason": self.stop_reason})())


@pytest.mark.asyncio
async def test_sdk_limits_are_explicit_and_chat_is_not_reused() -> None:
    sink = RecordingSink()
    created: list[ResultAgent] = []

    def build(_: Event) -> ResultAgent:
        fake = ResultAgent("limit_turns")
        created.append(fake)
        return fake

    navigator = StrandsNavigator(
        profile=profile(), checkpoints=sink, utc_now=lambda: NOW, agent_builder=build
    )
    first = await navigator.run_turn(projection())
    second = await navigator.run_turn(projection(phrase="Full name, edit text"))

    assert first.stop_reason is NavigatorStopReason.SDK_LIMIT
    assert second.stop_reason is NavigatorStopReason.SDK_LIMIT
    assert len(created) == 2, "each turn must use a fresh agent rather than a chat transcript"
    assert created[0].limits == [{"turns": 1, "output_tokens": 1024, "total_tokens": 12000}]
    assert "Email address, invalid entry" in created[0].prompts[0]
    assert "Full name, edit text" not in created[0].prompts[0]


@pytest.mark.asyncio
async def test_context_budget_stops_before_constructing_or_calling_an_agent() -> None:
    built = False

    def build(_: Event) -> ResultAgent:
        nonlocal built
        built = True
        return ResultAgent("end_turn")

    navigator = StrandsNavigator(
        profile=profile(max_context_characters=1000),
        checkpoints=RecordingSink(),
        utc_now=lambda: NOW,
        agent_builder=build,
    )
    result = await navigator.run_turn(projection(phrase="x" * 2000))
    assert result.stop_reason is NavigatorStopReason.CONTEXT_BUDGET_EXHAUSTED
    assert built is False


class SlowProposalSink(RecordingSink):
    async def retain(self, checkpoint: PlanningCheckpoint) -> None:
        self.items.append(checkpoint)
        if checkpoint.kind is CheckpointKind.ACTION_PROPOSED:
            await asyncio.sleep(10)


class ProposalAgent:
    def __init__(self, navigation_tool: NavigationActionTool) -> None:
        self._tool = navigation_tool

    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: Event,
    ) -> AgentResult:
        del prompt, limits, cancel_signal
        await self._tool.submit({"run_ref": RUN_REF, "action": ActionName.NEXT})
        return cast(AgentResult, type("Result", (), {"stop_reason": "end_turn"})())


@pytest.mark.asyncio
async def test_provider_timeout_after_proposal_closes_fence_without_dispatch_or_retry() -> None:
    sink = SlowProposalSink()
    dispatched: list[Any] = []
    built = 0

    def build(fence: Event) -> ProposalAgent:
        nonlocal built
        built += 1
        navigation_tool = make_navigation_tool(
            gateway=gateway(dispatched),
            checkpoints=sink,
            cancel_fence=fence,
            utc_now=lambda: NOW,
        )
        return ProposalAgent(navigation_tool)

    navigator = StrandsNavigator(
        profile=profile(call_timeout_seconds=1),
        checkpoints=sink,
        utc_now=lambda: NOW,
        agent_builder=build,
    )
    result = await navigator.run_turn(projection())

    assert result.stop_reason is NavigatorStopReason.PROVIDER_TIMEOUT
    assert built == 1
    assert dispatched == []
    assert [item.kind for item in sink.items] == [
        CheckpointKind.MODEL_CALL_STARTED,
        CheckpointKind.ACTION_PROPOSED,
        CheckpointKind.MODEL_CALL_STOPPED,
    ]
