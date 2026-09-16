"""Provider-independent, one-shot admission of a validated navigation proposal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from threading import Event

from accessforge_navigation_tools import NavigationGateway, ProposedAction, ToolRefusal

from .checkpoints import CheckpointKind, PlanningCheckpoint, PlanningCheckpointSink

UtcClock = Callable[[], str]


class NavigationActionSubmission:
    """Retain and dispatch one proposal without an agent SDK or provider tool registry."""

    def __init__(
        self,
        *,
        gateway: NavigationGateway,
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
