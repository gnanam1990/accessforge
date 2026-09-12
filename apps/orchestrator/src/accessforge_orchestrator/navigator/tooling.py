"""The navigator's sole Strands tool, with fail-closed input validation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Event
from typing import Any

from pydantic import ValidationError
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolSpec, ToolUse

from accessforge_navigation_tools import NavigationToolGateway, ProposedAction, ToolRefusal

from .checkpoints import CheckpointKind, PlanningCheckpoint, PlanningCheckpointSink

NAVIGATION_TOOL_NAME = "submit_navigation_action"
UtcClock = Callable[[], str]


class NavigationActionTool(AgentTool):
    """One narrow Strands capability with strict validation of the complete raw request."""

    def __init__(
        self,
        *,
        gateway: NavigationToolGateway,
        checkpoints: PlanningCheckpointSink,
        cancel_fence: Event,
        utc_now: UtcClock,
    ) -> None:
        super().__init__()
        self._gateway = gateway
        self._checkpoints = checkpoints
        self._cancel_fence = cancel_fence
        self._utc_now = utc_now
        self._claim_lock = asyncio.Lock()
        self._action_claimed = False

    @property
    def tool_name(self) -> str:
        return NAVIGATION_TOOL_NAME

    @property
    def tool_type(self) -> str:
        return "python"

    @property
    def tool_spec(self) -> ToolSpec:
        # Unlike the SDK's function-decorator schema, this schema includes
        # additionalProperties=false and the same sealed model validates the raw call at runtime.
        return {
            "name": NAVIGATION_TOOL_NAME,
            "description": (
                "Submit exactly one sealed screen-reader navigation action for supervisor "
                "admission. TYPE_TEXT takes a fixture-value reference, never raw text."
            ),
            "inputSchema": {"json": ProposedAction.model_json_schema(by_alias=False)},
        }

    async def _claim_once(self) -> None:
        async with self._claim_lock:
            if self._action_claimed:
                raise ToolRefusal(
                    "INVOCATION_ACTION_ALREADY_CLAIMED: wait for a fresh reader projection"
                )
            if self._cancel_fence.is_set():
                raise ToolRefusal("MODEL_CALL_CANCELLED: the invocation fence is closed")
            # Claim before validation. An invalid first request cannot be followed by a valid
            # request in the same model response and thereby turn schema probing into an action.
            self._action_claimed = True

    async def submit(self, raw_input: object) -> dict[str, str | None]:
        """Validate one complete raw model request and submit it below the prompt layer."""

        await self._claim_once()
        proposal = ProposedAction.model_validate(raw_input)

        # Retention is fail-closed: no proposal reaches the desktop if the durable checkpoint fails.
        await self._checkpoints.retain(
            PlanningCheckpoint(
                run_ref=proposal.run_ref,
                kind=CheckpointKind.ACTION_PROPOSED,
                recorded_at_utc=self._utc_now(),
                action=proposal.action,
                key_chord=proposal.key_chord,
                text_value_ref=proposal.text_value_ref,
            )
        )
        if self._cancel_fence.is_set():
            raise ToolRefusal("MODEL_CALL_CANCELLED: cancellation arrived after proposal retention")

        try:
            result = await self._gateway.submit(proposal)
        except ToolRefusal:
            raise
        except Exception:
            # A transport exception cannot establish whether the supervisor performed the effect.
            # Close this invocation before reporting AMBIGUOUS so the model cannot stack a retry on
            # top of an effect whose outcome is unknown.
            self._cancel_fence.set()
            await self._checkpoints.retain(
                PlanningCheckpoint(
                    run_ref=proposal.run_ref,
                    kind=CheckpointKind.ACTION_RESOLVED,
                    recorded_at_utc=self._utc_now(),
                    action=proposal.action,
                    key_chord=proposal.key_chord,
                    text_value_ref=proposal.text_value_ref,
                    dispatch_status="AMBIGUOUS",
                )
            )
            return {
                "status": "AMBIGUOUS",
                "actionId": None,
                "detail": "supervisor transport failed; effect outcome is unknown and fenced",
            }

        try:
            await self._checkpoints.retain(
                PlanningCheckpoint(
                    run_ref=proposal.run_ref,
                    kind=CheckpointKind.ACTION_RESOLVED,
                    recorded_at_utc=self._utc_now(),
                    action=proposal.action,
                    key_chord=proposal.key_chord,
                    text_value_ref=proposal.text_value_ref,
                    dispatch_status=result.status,
                    action_id=result.action_id,
                )
            )
        except Exception:
            # The supervisor journal is the effect record. If the secondary planning checkpoint
            # cannot be retained after dispatch, the model must not retry the effect.
            self._cancel_fence.set()
            return {
                "status": "AMBIGUOUS",
                "actionId": result.action_id,
                "detail": "dispatch completed but planning-result retention failed; fenced",
            }
        if result.status == "AMBIGUOUS":
            self._cancel_fence.set()
        return {
            "status": result.status,
            "actionId": result.action_id,
            "detail": result.detail,
        }

    async def stream(
        self,
        tool_use: ToolUse,
        invocation_state: dict[str, Any],
        **kwargs: Any,
    ) -> ToolGenerator:
        del invocation_state, kwargs
        tool_use_id = tool_use.get("toolUseId", "unknown")
        try:
            result = await self.submit(tool_use.get("input", {}))
        except (ToolRefusal, ValidationError, ValueError) as exc:
            yield ToolResultEvent(
                {
                    "toolUseId": tool_use_id,
                    "status": "error",
                    "content": [{"text": f"Error: {type(exc).__name__}: {exc}"}],
                },
                exception=exc,
            )
        except Exception as exc:
            # Provider/database exceptions can contain endpoints or credential-bearing URLs.
            yield ToolResultEvent(
                {
                    "toolUseId": tool_use_id,
                    "status": "error",
                    "content": [{"text": f"Error: {type(exc).__name__}"}],
                },
                exception=exc,
            )
        else:
            yield ToolResultEvent(
                {
                    "toolUseId": tool_use_id,
                    "status": "success",
                    "content": [{"json": result}],
                }
            )


def make_navigation_tool(
    *,
    gateway: NavigationToolGateway,
    checkpoints: PlanningCheckpointSink,
    cancel_fence: Event,
    utc_now: UtcClock,
) -> NavigationActionTool:
    return NavigationActionTool(
        gateway=gateway,
        checkpoints=checkpoints,
        cancel_fence=cancel_fence,
        utc_now=utc_now,
    )
