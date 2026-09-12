"""Pinned provider identity and finite navigator budgets."""

from __future__ import annotations

from importlib.metadata import version
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PINNED_STRANDS_VERSION = "1.55.1"
PINNED_NAVIGATOR_MODEL_ID = "global.anthropic.claude-sonnet-4-6"
PINNED_NAVIGATOR_REGION = "us-east-1"


def installed_strands_version() -> str:
    """Return the installed distribution version without contacting a provider."""

    return version("strands-agents")


class NavigatorModelProfile(BaseModel):
    """The entire model identity and per-invocation resource envelope.

    Provider credentials intentionally are not fields. The orchestrator process receives only the
    provider credential chain needed by Bedrock; runner, repository, GitHub, observer and target
    credentials are not part of this package's input contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sdk_distribution: Literal["strands-agents"] = "strands-agents"
    sdk_version: Literal["1.55.1"] = "1.55.1"
    provider: Literal["amazon-bedrock"] = "amazon-bedrock"
    model_id: Literal["global.anthropic.claude-sonnet-4-6"] = "global.anthropic.claude-sonnet-4-6"
    region_name: Literal["us-east-1"] = "us-east-1"
    temperature: Literal[0] = 0
    provider_max_tokens: int = Field(default=512, ge=64, le=4096)
    # One model cycle may emit one or more tool uses in a single response, but it never receives a
    # second model turn on a mere dispatch result. The caller must first supply the next retained
    # actual-reader observation in a fresh projection.
    invocation_turns: Literal[1] = 1
    invocation_output_tokens: int = Field(default=1024, ge=64, le=4096)
    invocation_total_tokens: int = Field(default=12000, ge=512, le=50000)
    max_context_characters: int = Field(default=24000, ge=1000, le=100000)
    call_timeout_seconds: float = Field(default=30, gt=0, le=120)
    model_attempts: int = Field(default=2, ge=1, le=3)
    retry_initial_delay_seconds: int = Field(default=1, ge=1, le=5)
    retry_max_delay_seconds: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def retry_delays_are_ordered(self) -> Self:
        if self.retry_max_delay_seconds < self.retry_initial_delay_seconds:
            raise ValueError("retry max delay cannot be below the initial delay")
        return self

    def assert_installed_sdk(self) -> None:
        observed = installed_strands_version()
        if observed != self.sdk_version:
            detail = (
                f"strands-agents {observed} is installed; "
                f"sealed profile requires {self.sdk_version}"
            )
            raise RuntimeError(detail)
