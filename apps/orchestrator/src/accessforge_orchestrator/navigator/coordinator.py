"""Same-host, one-shot production composition for explicitly consented navigator calls.

Construction never starts AT, invokes a provider, or grants consent. The caller supplies the
private capability returned by the already approved native bootstrap. This object owns that
capability for its entire lifetime; neither a persisted operation nor a chat can reconstruct it.
There is no worker retry, environment discovery, provider fallback, or automatic desktop release.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from accessforge_domain.canonical import digest
from accessforge_domain.codex_navigation import validate_profile as validate_codex_profile
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_navigation_tools import (
    ActionName,
    DispatchResult,
    ProposedAction,
    SupervisorDispatchRequest,
    ToolRefusal,
)
from accessforge_orchestrator.manual_dispatch import DispatchReference
from accessforge_persistence import navigator_model_calls, navigator_runtime, workspace_connection

from .checkpoints import CheckpointKind, PlanningCheckpoint
from .codex import CodexNavigationProfile, CodexNavigator
from .config import NavigatorModelProfile
from .native_transport import NativeNavigatorTransport
from .postgres import PostgresPlanningCheckpointSink
from .projection import RetainedNavigatorTurn, load_retained_turn
from .results import NavigatorInvocationResult, NavigatorStopReason

if TYPE_CHECKING:
    from .agent import NavigatorAgent


def build_strands_agent(**kwargs: Any) -> NavigatorAgent:
    """Load the retired compatibility entrypoint only on an explicit legacy path."""
    from .agent import build_strands_agent as legacy_builder

    return legacy_builder(**kwargs)


@dataclass(frozen=True, slots=True)
class AdmittedTurnResult:
    operation_id: str
    invocation: NavigatorInvocationResult
    disposition: Literal["RECORDED", "UNCONFIRMED", "NOT_CALLED"]
    next_action_sequence: int | None
    stop_acknowledged: bool = False


class _InvocationCheckpoints:
    def __init__(self, sink: PostgresPlanningCheckpointSink) -> None:
        self._sink = sink
        self.resolved: PlanningCheckpoint | None = None

    async def retain(self, checkpoint: PlanningCheckpoint) -> None:
        await self._sink.retain(checkpoint)
        if checkpoint.kind is CheckpointKind.ACTION_RESOLVED:
            self.resolved = checkpoint


class _NativeGateway:
    """No invented origin/runtime snapshot: the native host owns the final physical gate."""

    def __init__(
        self,
        session: NativeNavigatorSession,
        turn: RetainedNavigatorTurn,
        operation_id: str,
        request_digest: str,
    ) -> None:
        self._session, self._turn = session, turn
        self._operation_id, self._request_digest = operation_id, request_digest

    async def submit(self, proposal: ProposedAction) -> DispatchResult:
        policy = self._turn.projection.policy
        if (
            proposal.run_ref != self._turn.projection.run_ref
            or proposal.action not in policy.allowed_actions
            or (
                proposal.action is ActionName.KEY_CHORD
                and proposal.key_chord not in policy.allowed_key_chords
            )
            or (
                proposal.action is ActionName.TYPE_TEXT
                and proposal.text_value_ref not in policy.fixture_values
            )
        ):
            raise ToolRefusal("proposal differs from this invocation's sealed policy")
        self._session._recheck(self._turn, self._operation_id, self._request_digest)
        # Only value names cross this port. Native bootstrap has already frozen the independent
        # sealed fixture and repeats actual focus, origin, lease and action-budget admission.
        return await self._session._transport(
            SupervisorDispatchRequest(
                run_ref=proposal.run_ref,
                action=proposal.action.value,
                key_chord=proposal.key_chord,
                text_value_ref=proposal.text_value_ref,
            )
        )


class NativeNavigatorSession:
    """Own one original native capability, then admit at most one call per reader boundary.

    run_next_turn may be called again only after a known non-STOP action, when the supervisor's
    original reader record is retained. A not-yet-retained boundary may be polled before admission;
    after reservation, any exception consumes the turn permanently. close fences future input but
    is NOT evidence of OS quiescence, observer closure, or successful run finalization.
    """

    def __init__(
        self,
        *,
        database_url: str,
        reference: DispatchReference,
        consent_id: str,
        profile: NavigatorModelProfile | CodexNavigationProfile,
        private_reference: dict[str, Any],
    ) -> None:
        self._database_url, self._reference = database_url, reference
        self._consent_id, self._profile = consent_id, profile
        self._sequence = 0
        self._busy = False
        self._fence = Event()
        self._transport = NativeNavigatorTransport(
            private_reference=private_reference,
            expected=reference,
            run_ref="navigator:" + digest(asdict(reference)),
        )

    def close(self) -> None:
        self._fence.set()
        self._transport.close()

    def _load(self) -> RetainedNavigatorTurn:
        return load_retained_turn(
            database_url=self._database_url,
            reference=self._reference,
            expected_action_sequence=self._sequence,
        )

    def _recheck(self, turn: RetainedNavigatorTurn, operation_id: str, request_digest: str) -> None:
        if self._fence.is_set():
            raise ToolRefusal("navigator session is permanently fenced")
        fresh = self._load()
        if fresh != turn:
            raise ToolRefusal("original reader projection changed after reservation")
        with workspace_connection(self._database_url, self._reference.workspace_id) as conn:
            navigator_model_calls.assert_turn_authorized(
                conn,
                **asdict(self._reference),
                consent_id=self._consent_id,
                operation_id=operation_id,
                request_digest=request_digest,
                model_config_digest=digest(self._profile.model_dump(mode="json")),
                projection_digest=digest(turn.projection.model_payload()),
                action_sequence=self._sequence,
            )
        if self._fence.is_set():
            raise ToolRefusal("navigator was cancelled during authority recheck")

    async def run_next_turn(self) -> AdmittedTurnResult:
        if self._busy or self._fence.is_set():
            raise ToolRefusal("navigator is busy or permanently fenced; no concurrent invocation")
        self._busy = True
        operation_id, request_digest = str(uuid4()), None
        provider_entered = False
        disposition: Literal["RECORDED", "UNCONFIRMED", "NOT_CALLED"] = "NOT_CALLED"
        try:
            if isinstance(self._profile, CodexNavigationProfile):
                validate_codex_profile(self._profile.model_dump(mode="json"))
            else:
                self._profile.assert_installed_sdk()
            turn = self._load()
            model_digest = digest(self._profile.model_dump(mode="json"))
            if turn.model_config_digest != model_digest:
                raise ToolRefusal("configured provider profile differs from the sealed manifest")
            # No suspension point between committing reservation and recording its handle. A
            # cancellation or unknown commit cannot proceed to construction; durable admission
            # refuses any subsequent duplicate, including a fresh process/operation UUID.
            with workspace_connection(self._database_url, self._reference.workspace_id) as conn:
                reserved_digest = navigator_model_calls.reserve_turn(
                    conn,
                    **asdict(self._reference),
                    consent_id=self._consent_id,
                    operation_id=operation_id,
                    model_config_digest=model_digest,
                    projection_digest=digest(turn.projection.model_payload()),
                    action_sequence=self._sequence,
                    reader_records=tuple(
                        zip(turn.reader_event_ids, turn.reader_payload_digests, strict=True)
                    ),
                )
            request_digest = reserved_digest
            sink = _InvocationCheckpoints(
                PostgresPlanningCheckpointSink(
                    database_url=self._database_url,
                    workspace_id=self._reference.workspace_id,
                    run_id=self._reference.run_id,
                    attempt_id=self._reference.attempt_id,
                    expected_run_ref=turn.projection.run_ref,
                    operation_id=operation_id,
                )
            )
            gateway = _NativeGateway(self, turn, operation_id, request_digest)

            def utc_now() -> str:
                return to_rfc3339_utc(datetime.now(UTC))

            def build(fence: Event) -> NavigatorAgent:
                nonlocal provider_entered, disposition
                assert isinstance(self._profile, NavigatorModelProfile)
                self._recheck(turn, operation_id, reserved_digest)
                # Construction may resolve credentials/contact provider infrastructure. From this
                # point on, unknown/error/cancellation can never release the hold as NOT_CALLED.
                provider_entered, disposition = True, "UNCONFIRMED"
                return build_strands_agent(
                    profile=self._profile,
                    gateway=gateway,
                    checkpoints=sink,
                    cancel_fence=fence,
                    utc_now=utc_now,
                )

            if isinstance(self._profile, CodexNavigationProfile):

                def authorize_codex() -> None:
                    nonlocal provider_entered, disposition
                    self._recheck(turn, operation_id, reserved_digest)
                    provider_entered, disposition = True, "UNCONFIRMED"

                outcome = await CodexNavigator(
                    profile=self._profile,
                    gateway=gateway,
                    checkpoints=sink,
                    utc_now=utc_now,
                    authorize_invocation=authorize_codex,
                ).run_turn(turn.projection, cancel_signal=self._fence)
            else:
                from .agent import StrandsNavigator

                outcome = await StrandsNavigator(
                    profile=self._profile, checkpoints=sink, utc_now=utc_now, agent_builder=build
                ).run_turn(turn.projection, cancel_signal=self._fence)
            if outcome.runtime_observation is not None:
                with workspace_connection(self._database_url, self._reference.workspace_id) as conn:
                    navigator_runtime.retain(
                        conn,
                        workspace_id=self._reference.workspace_id,
                        operation_id=operation_id,
                        observation=outcome.runtime_observation,
                    )
            if provider_entered and outcome.stop_reason in {
                NavigatorStopReason.COMPLETED,
                NavigatorStopReason.SDK_LIMIT,
            }:
                disposition = "RECORDED"
            action = sink.resolved
            can_continue = (
                disposition == "RECORDED"
                and not self._fence.is_set()
                and action is not None
                and action.action is not ActionName.STOP
                and action.dispatch_status in {"SUCCEEDED", "FAILED"}
                and action.action_id is not None
            )
            if can_continue:
                self._sequence += 1
            else:
                self.close()
            return AdmittedTurnResult(
                operation_id,
                outcome,
                disposition,
                self._sequence if can_continue else None,
                disposition == "RECORDED"
                and action is not None
                and action.action is ActionName.STOP
                and action.dispatch_status == "SUCCEEDED",
            )
        except BaseException:
            # A pre-admission reader-not-ready refusal is safe to poll. Once the reservation
            # commits, this object never tries that boundary or transport again.
            if request_digest is not None:
                self.close()
            raise
        finally:
            try:
                if request_digest is not None:
                    with workspace_connection(
                        self._database_url, self._reference.workspace_id
                    ) as conn:
                        navigator_model_calls.finish_turn(
                            conn,
                            workspace_id=self._reference.workspace_id,
                            operation_id=operation_id,
                            request_digest=request_digest,
                            status=disposition,
                        )
            except BaseException:
                # Unknown final accounting stays STARTED and holds the full reservation. Do not
                # return a successful continuation or replay completion to repair its status.
                self.close()
                raise
            finally:
                self._busy = False
