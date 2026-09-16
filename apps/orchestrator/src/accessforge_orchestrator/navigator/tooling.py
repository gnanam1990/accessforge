"""Historical Strands adapter around the provider-independent action gateway."""

from __future__ import annotations

from threading import Event
from typing import Any

from pydantic import ValidationError
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolSpec, ToolUse

from accessforge_navigation_tools import NavigationGateway, ProposedAction, ToolRefusal

from .checkpoints import PlanningCheckpointSink
from .submission import NavigationActionSubmission
from .submission import UtcClock as UtcClock

NAVIGATION_TOOL_NAME = "submit_navigation_action"


class NavigationActionTool(NavigationActionSubmission, AgentTool):
    """Compatibility adapter; production Codex uses NavigationActionSubmission directly."""

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
    gateway: NavigationGateway,
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
