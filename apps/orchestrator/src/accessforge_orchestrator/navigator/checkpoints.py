"""Privacy-safe planning checkpoint contract.

The production sink is supplied by the control plane and must durably commit before returning.
There is deliberately no filesystem or chat-transcript fallback in this package.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from accessforge_navigation_tools import ActionName


class CheckpointKind(StrEnum):
    MODEL_CALL_STARTED = "MODEL_CALL_STARTED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    ACTION_RESOLVED = "ACTION_RESOLVED"
    MODEL_CALL_STOPPED = "MODEL_CALL_STOPPED"
    NAVIGATOR_STOPPED = "NAVIGATOR_STOPPED"


class PlanningCheckpoint(BaseModel):
    """A retained decision record with no announcement text or resolved fixture value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_ref: str = Field(min_length=1)
    kind: CheckpointKind
    recorded_at_utc: str = Field(min_length=1)
    sdk_version: str | None = None
    provider: str | None = None
    model_id: str | None = None
    action: ActionName | None = None
    key_chord: str | None = None
    text_value_ref: str | None = None
    dispatch_status: Literal["SUCCEEDED", "FAILED", "AMBIGUOUS", "REFUSED"] | None = None
    action_id: str | None = None
    stop_reason: (
        Literal[
            "COMPLETED",
            "SDK_LIMIT",
            "PROVIDER_TIMEOUT",
            "CANCELLED",
            "CONTEXT_BUDGET_EXHAUSTED",
            "PROVIDER_ERROR",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def fields_match_checkpoint_kind(self) -> Self:
        model_kind = self.kind in {
            CheckpointKind.MODEL_CALL_STARTED,
            CheckpointKind.MODEL_CALL_STOPPED,
            CheckpointKind.NAVIGATOR_STOPPED,
        }
        model_identity = (self.sdk_version, self.provider, self.model_id)
        if (model_kind and not all(model_identity)) or (
            not model_kind and any(value is not None for value in model_identity)
        ):
            raise ValueError("model checkpoints require model identity and other kinds forbid it")

        action_kind = self.kind in {
            CheckpointKind.ACTION_PROPOSED,
            CheckpointKind.ACTION_RESOLVED,
        }
        if action_kind != (self.action is not None):
            raise ValueError("action checkpoints require an action and other kinds forbid it")
        if (self.kind is CheckpointKind.ACTION_RESOLVED) != (self.dispatch_status is not None):
            raise ValueError("only a resolved action carries dispatch status")
        stopped_kind = self.kind in {
            CheckpointKind.MODEL_CALL_STOPPED,
            CheckpointKind.NAVIGATOR_STOPPED,
        }
        if stopped_kind != (self.stop_reason is not None):
            raise ValueError("only a stopped navigator lifecycle carries stop reason")

        if self.action is ActionName.KEY_CHORD:
            if self.key_chord is None or self.text_value_ref is not None:
                raise ValueError("KEY_CHORD checkpoint requires only key_chord")
        elif self.action is ActionName.TYPE_TEXT:
            if self.text_value_ref is None or self.key_chord is not None:
                raise ValueError("TYPE_TEXT checkpoint requires only text_value_ref")
        elif self.key_chord is not None or self.text_value_ref is not None:
            raise ValueError("action arguments do not match the checkpoint action")

        if self.kind is not CheckpointKind.ACTION_RESOLVED and self.action_id is not None:
            raise ValueError("only a resolved action may carry an action identifier")
        return self


class PlanningCheckpointSink(Protocol):
    """Durably retain a checkpoint, or raise so the action remains fenced."""

    async def retain(self, checkpoint: PlanningCheckpoint) -> None: ...
