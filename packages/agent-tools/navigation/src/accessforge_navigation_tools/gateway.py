"""The enforcement layer below Strands prompts and model output."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from accessforge_domain.runners.gate import (
    ActionRequest,
    LeaseView,
    evaluate_action,
)

from .models import ActionName, ProposedAction, SealedNavigatorPolicy


class ToolRefusal(RuntimeError):
    """A model proposal was not admitted to the supervisor dispatch boundary."""


@dataclass(frozen=True, slots=True)
class NavigationRuntimeState:
    lease_id: str
    lease_epoch: int
    current_epoch: int
    deadline_monotonic: float
    cancel_requested: bool
    action_in_flight: bool
    now_monotonic: float
    now_utc: str
    actions_used: int
    wall_time_used_seconds: float
    observed_origin: str


@dataclass(frozen=True, slots=True)
class SupervisorDispatchRequest:
    run_ref: str
    action: str
    key_chord: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    status: Literal["SUCCEEDED", "FAILED", "AMBIGUOUS", "REFUSED"]
    action_id: str | None = None
    detail: str = ""


StateProvider = Callable[[], NavigationRuntimeState]
SupervisorDispatch = Callable[[SupervisorDispatchRequest], Awaitable[DispatchResult]]


class NavigationToolGateway:
    """Resolve a validated proposal and recheck dynamic authority immediately before dispatch."""

    def __init__(
        self,
        *,
        run_ref: str,
        platform: str,
        permitted_origins: frozenset[str],
        policy: SealedNavigatorPolicy,
        state_provider: StateProvider,
        dispatch: SupervisorDispatch,
    ) -> None:
        self._run_ref = run_ref
        self._platform = platform
        self._permitted_origins = permitted_origins
        self._policy = policy
        self._state_provider = state_provider
        self._dispatch = dispatch

    async def submit(self, proposal: ProposedAction) -> DispatchResult:
        if proposal.run_ref != self._run_ref:
            detail = (
                f"proposal names {proposal.run_ref!r}, "
                f"not the sealed run reference {self._run_ref!r}"
            )
            raise ToolRefusal(detail)

        text: str | None = None
        if proposal.action is ActionName.TYPE_TEXT:
            ref = proposal.text_value_ref
            if ref is None or ref not in self._policy.fixture_values:
                raise ToolRefusal(f"text value reference {ref!r} is not in the sealed fixture")
            text = self._policy.fixture_values[ref]

        runtime = self._state_provider()
        lease = LeaseView(
            lease_id=runtime.lease_id,
            epoch=runtime.lease_epoch,
            current_epoch=runtime.current_epoch,
            deadline_monotonic=runtime.deadline_monotonic,
            cancel_requested=runtime.cancel_requested,
            action_in_flight=runtime.action_in_flight,
        )
        decision = evaluate_action(
            lease,
            ActionRequest(
                action=proposal.action.value,
                now_monotonic=runtime.now_monotonic,
                now_utc=runtime.now_utc,
                actions_used=runtime.actions_used,
                max_actions=self._policy.max_actions,
                wall_time_used_seconds=runtime.wall_time_used_seconds,
                max_wall_time_seconds=self._policy.wall_time_seconds,
                platform=self._platform,
                origin=runtime.observed_origin,
                permitted_origins=self._permitted_origins,
                key_chord=proposal.key_chord,
                text=text,
            ),
        )
        if not decision.admitted:
            reason = decision.refusal.value if decision.refusal is not None else "UNKNOWN_REFUSAL"
            raise ToolRefusal(f"{reason}: {decision.detail}")

        # The real supervisor repeats lease/cancellation/origin/budget checks atomically with its
        # durable intent write. This boundary's recheck closes the delayed-model window; it does not
        # pretend an in-process snapshot can replace the supervisor's final authority decision.
        return await self._dispatch(
            SupervisorDispatchRequest(
                run_ref=self._run_ref,
                action=proposal.action.value,
                key_chord=proposal.key_chord,
                text=text,
            )
        )
