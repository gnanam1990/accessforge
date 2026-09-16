"""Provider-independent navigator invocation results."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class NavigatorStopReason(StrEnum):
    COMPLETED = "COMPLETED"
    SDK_LIMIT = "SDK_LIMIT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    CANCELLED = "CANCELLED"
    CONTEXT_BUDGET_EXHAUSTED = "CONTEXT_BUDGET_EXHAUSTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class NavigatorInvocationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_reason: NavigatorStopReason
    provider_stop_reason: str | None = None
    detail: str = ""
    runtime_observation: dict[str, Any] | None = None
