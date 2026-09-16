"""Bounded navigation; retired SDK adapters load only when explicitly requested."""

from typing import TYPE_CHECKING, Any

from .checkpoints import CheckpointKind, PlanningCheckpoint, PlanningCheckpointSink
from .config import NavigatorModelProfile, installed_strands_version
from .coordinator import AdmittedTurnResult, NativeNavigatorSession
from .postgres import PostgresPlanningCheckpointSink
from .results import NavigatorInvocationResult, NavigatorStopReason

if TYPE_CHECKING:
    from .agent import StrandsNavigator, build_strands_agent
    from .tooling import NAVIGATION_TOOL_NAME, NavigationActionTool, make_navigation_tool


def __getattr__(name: str) -> Any:
    if name in {"StrandsNavigator", "build_strands_agent"}:
        from . import agent

        return getattr(agent, name)
    if name in {"NAVIGATION_TOOL_NAME", "NavigationActionTool", "make_navigation_tool"}:
        from . import tooling

        return getattr(tooling, name)
    raise AttributeError(name)


__all__ = [
    "NAVIGATION_TOOL_NAME",
    "AdmittedTurnResult",
    "NativeNavigatorSession",
    "NavigationActionTool",
    "CheckpointKind",
    "NavigatorInvocationResult",
    "NavigatorModelProfile",
    "NavigatorStopReason",
    "PlanningCheckpoint",
    "PlanningCheckpointSink",
    "PostgresPlanningCheckpointSink",
    "StrandsNavigator",
    "build_strands_agent",
    "installed_strands_version",
    "make_navigation_tool",
]
