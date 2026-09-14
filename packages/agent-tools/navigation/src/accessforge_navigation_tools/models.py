"""Strict data contracts at the navigator's observation and proposal boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS
from accessforge_domain.reference_destination import reference_destination


class ActionName(StrEnum):
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"
    ACTIVATE = "ACTIVATE"
    TYPE_TEXT = "TYPE_TEXT"
    KEY_CHORD = "KEY_CHORD"
    READ_CURRENT = "READ_CURRENT"
    WAIT_FOR_READER_IDLE = "WAIT_FOR_READER_IDLE"
    STOP = "STOP"


class _SealedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ReaderObservation(_SealedModel):
    """The complete reader channel. No extensible metadata bag exists."""

    phrase: str | None = Field(default=None, max_length=4096)
    captured_at_utc: str = Field(alias="capturedAtUtc", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    action_sequence: int = Field(alias="actionSequence", ge=1)
    provenance: Literal["ACTUAL_READER", "CAPTURE_UNKNOWN"]
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def evidence_shape_matches_provenance(self) -> Self:
        if self.provenance == "ACTUAL_READER" and self.phrase is None:
            raise ValueError(
                "actual reader evidence requires a phrase, including genuine silence as ''"
            )
        if self.provenance == "CAPTURE_UNKNOWN" and self.phrase is not None:
            raise ValueError("capture-unknown evidence must not manufacture a phrase")
        if self.provenance == "CAPTURE_UNKNOWN" and not self.reason:
            raise ValueError("capture-unknown evidence requires a reason")
        return self


class SealedNavigatorPolicy(_SealedModel):
    task_summary: str = Field(alias="taskSummary", min_length=1, max_length=2000)
    success_condition: str = Field(alias="successCondition", min_length=1, max_length=2000)
    start_url: str = Field(alias="startUrl", min_length=1)
    allowed_actions: tuple[ActionName, ...] = Field(alias="allowedActions", min_length=1)
    allowed_key_chords: tuple[str, ...] = Field(alias="allowedKeyChords")
    max_actions: int = Field(alias="maxActions", ge=1, le=500)
    wall_time_seconds: int = Field(alias="wallTimeSeconds", ge=1, le=1800)
    fixture_values: dict[str, str] = Field(alias="fixtureValues")
    forbidden_observations: tuple[
        Literal[
            "DOM",
            "SELECTORS",
            "SCREENSHOTS",
            "SOURCE",
            "OBSERVER_RECEIPTS",
            "ASSERTION_EXPECTATIONS",
        ],
        ...,
    ] = Field(alias="forbiddenObservations")

    @model_validator(mode="after")
    def policy_is_complete_not_selectively_weakened(self) -> Self:
        action_values = {action.value for action in self.allowed_actions}
        unsupported = action_values - ALLOWED_ACTIONS
        if unsupported:
            raise ValueError(
                f"navigator actions {sorted(unsupported)} are outside the sealed domain policy"
            )
        required_forbidden = {
            "DOM",
            "SELECTORS",
            "SCREENSHOTS",
            "SOURCE",
            "OBSERVER_RECEIPTS",
            "ASSERTION_EXPECTATIONS",
        }
        if set(self.forbidden_observations) != required_forbidden:
            raise ValueError("navigator policy must carry every forbidden observation category")
        return self


class NavigatorProjection(_SealedModel):
    """Only approved intent, safe fixtures and actual reader observations."""

    run_ref: str = Field(alias="runRef", min_length=1)
    policy: SealedNavigatorPolicy
    reader_observations: tuple[ReaderObservation, ...] = Field(alias="readerObservations")
    runtime_start_url: str | None = Field(default=None, alias="runtimeStartUrl", max_length=2048)

    @model_validator(mode="after")
    def destination_preserves_original_policy(self) -> Self:
        if self.runtime_start_url is not None:
            origin, separator, nonce = self.runtime_start_url.rpartition("/form/")
            if (
                not separator
                or reference_destination(
                    sealed_url=self.policy.start_url, origin=origin, nonce=nonce
                )
                != self.runtime_start_url
            ):
                raise ValueError("runtime destination differs from the reviewed template")
        return self

    @classmethod
    def from_policy(
        cls,
        *,
        run_ref: str,
        policy: dict[str, object],
        reader_observations: list[ReaderObservation],
        runtime_start_url: str | None = None,
    ) -> NavigatorProjection:
        # Validate the whole sealed policy first. Extra oracle/source keys are rejected rather than
        # copied and then filtered, so an upstream boundary expansion is a visible failure.
        sealed = SealedNavigatorPolicy.model_validate(policy)
        return cls.model_validate(
            {
                "run_ref": run_ref,
                "policy": sealed,
                "reader_observations": tuple(reader_observations),
                "runtime_start_url": runtime_start_url,
            }
        )

    def model_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", by_alias=True)
        if self.runtime_start_url is None:
            del payload["runtimeStartUrl"]
        return payload


class ProposedAction(_SealedModel):
    """One model proposal. TYPE_TEXT names a sealed fixture value; it never carries raw text."""

    run_ref: str = Field(alias="runRef", min_length=1)
    action: ActionName
    key_chord: str | None = Field(
        default=None,
        validation_alias=AliasChoices("keyChord", "key_chord"),
        serialization_alias="keyChord",
    )
    text_value_ref: str | None = Field(
        default=None,
        validation_alias=AliasChoices("textValueRef", "text_value_ref"),
        serialization_alias="textValueRef",
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )

    @model_validator(mode="after")
    def arguments_match_action(self) -> Self:
        if self.action is ActionName.TYPE_TEXT:
            if self.text_value_ref is None or self.key_chord is not None:
                raise ValueError("TYPE_TEXT requires only textValueRef")
        elif self.action is ActionName.KEY_CHORD:
            if self.key_chord is None or self.text_value_ref is not None:
                raise ValueError("KEY_CHORD requires only keyChord")
        elif self.key_chord is not None or self.text_value_ref is not None:
            raise ValueError(f"{self.action} accepts no action arguments")
        return self
