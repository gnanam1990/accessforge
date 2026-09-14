"""Observe actual SDK request boundaries without retaining model content or credentials."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from threading import Event, RLock
from typing import Any

from strands import Agent
from strands.models import BedrockModel
from strands.types.agent import Limits

from accessforge_domain.navigator_model import validate_profile
from accessforge_domain.navigator_runtime import MEANING, validate_observation

from .config import NavigatorModelProfile, installed_strands_version


class ObservedBedrockModel(BedrockModel):
    """One fresh model per invocation. Hooks bound every SDK fallback and configured retry.

    The SDK worker can outlive cancellation. A closed fence forbids any later request and an active
    worker cannot mint a completed observation. No prompt, response body or credential is copied.
    """

    def arm(
        self,
        agent: Agent,
        *,
        limits: Limits,
        timeout: float,
        context_limit: int,
        fence: Event,
        expected: NavigatorModelProfile,
    ) -> None:
        if hasattr(self, "_audit_lock"):
            raise RuntimeError("runtime observation cannot be reset for another invocation")
        self._audit_lock = RLock()
        self._audit_fence = fence
        self._audit_active = 0
        self._audit_invalid = False
        self._audit_requests: list[dict[str, Any]] = []
        retry = agent._retry_strategy
        self._audit_profile = {
            "sdk_distribution": "strands-agents",
            "sdk_version": installed_strands_version(),
            "provider": "amazon-bedrock",
            "model_id": self.config.get("model_id"),
            "region_name": self.client.meta.region_name,
            "temperature": self.config.get("temperature"),
            "provider_max_tokens": self.config.get("max_tokens"),
            "invocation_turns": limits.get("turns"),
            "invocation_output_tokens": limits.get("output_tokens"),
            "invocation_total_tokens": limits.get("total_tokens"),
            "max_context_characters": context_limit,
            "call_timeout_seconds": timeout,
            "model_attempts": retry._max_attempts,
            "retry_initial_delay_seconds": retry._initial_delay,
            "retry_max_delay_seconds": retry._max_delay,
        }
        validate_profile(self._audit_profile)
        if self._audit_profile != expected.model_dump(mode="json"):
            raise RuntimeError(
                "constructed model or invocation limits differ from reviewed profile"
            )
        self.client.meta.events.register(
            "before-parameter-build.bedrock-runtime.*", self._before_request
        )
        self.client.meta.events.register("after-call.bedrock-runtime.*", self._after_response)

    def _before_request(self, params: dict[str, Any], model: Any, **kwargs: Any) -> None:
        with self._audit_lock:
            profile, meta = self._audit_profile, self.client.meta
            inference = params.get("inferenceConfig", {})
            if (
                self._audit_fence.is_set()
                or self._audit_invalid
                or self._audit_active != 1
                or len(self._audit_requests) >= profile["model_attempts"]
                or model.name != "ConverseStream"
                or not set(params)
                <= {"modelId", "messages", "system", "toolConfig", "inferenceConfig"}
                or params.get("modelId") != profile["model_id"]
                or inference != {"maxTokens": profile["provider_max_tokens"], "temperature": 0}
                or meta.region_name != profile["region_name"]
                or meta.endpoint_url
                != f"https://bedrock-runtime.{profile['region_name']}.amazonaws.com"
                or meta.config.retries != {"total_max_attempts": 1, "mode": "standard"}
                or meta.config.connect_timeout != min(10, profile["call_timeout_seconds"])
                or meta.config.read_timeout != profile["call_timeout_seconds"]
            ):
                self._audit_invalid = True
                raise RuntimeError("provider request differs from the bounded observed runtime")
            self._audit_requests.append(
                {"requestId": None, "httpStatus": None, "streamCompleted": False}
            )

    def _after_response(self, parsed: dict[str, Any], **kwargs: Any) -> None:
        with self._audit_lock:
            if not self._audit_requests:
                self._audit_invalid = True
                return
            metadata = parsed.get("ResponseMetadata", {})
            self._audit_requests[-1].update(
                requestId=metadata.get("RequestId"), httpStatus=metadata.get("HTTPStatusCode")
            )

    def _stream(self, callback: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        # The base implementation runs in its own worker thread in pinned Strands 1.55.1.
        with self._audit_lock:
            self._audit_active += 1
        stopped, metadata = False, False

        def observed(event: Any = None) -> None:
            nonlocal stopped, metadata
            if isinstance(event, dict):
                stopped = stopped or "messageStop" in event
                metadata = metadata or "metadata" in event
            callback(event)

        try:
            super()._stream(observed, *args, **kwargs)
            with self._audit_lock:
                if stopped and metadata and not self._audit_fence.is_set() and self._audit_requests:
                    self._audit_requests[-1]["streamCompleted"] = True
        finally:
            with self._audit_lock:
                self._audit_active -= 1

    def observation(self) -> dict[str, Any] | None:
        with self._audit_lock:
            if self._audit_active or self._audit_invalid or self._audit_fence.is_set():
                return None
            value = deepcopy(
                {
                    "meaning": MEANING,
                    "profile": self._audit_profile,
                    "requests": self._audit_requests,
                }
            )
        try:
            return validate_observation(value)
        except ValueError:
            return None
