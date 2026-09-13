"""One-shot manual attempt handoff, not an action or an actual-reader attestation.

The run transition and its outbox are the durable dispatch claim. Only the caller that commits
LEASED -> RUNNING may send the reference; a replay never sends again. Transport acknowledgement
means only that the reference arrived. The receiving supervisor must independently validate live
authority, deployed identity, local fencing and every action before touching the OS.

No production transport is enabled here: the actual-reader capability gate is still closed.
An injected transport is trusted controller infrastructure, never a navigator-supplied callback.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from accessforge_domain import reducers
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_persistence import runners, runs, supervisor_dispatch, workspace_connection
from accessforge_persistence.supervisor_dispatch import DispatchTicket


@dataclass(frozen=True, slots=True)
class DispatchReference:
    workspace_id: str
    run_id: str
    attempt_id: str
    runner_id: str
    lease_id: str
    epoch: int


class StartTransport(Protocol):
    def check_available(self) -> None:
        """Refuse before admission if authenticated, actual-reader transport is unavailable."""

    async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
        """Deliver once; re-read the reference, never treat it as an executable command."""


class ReaderTransportUnavailable(RuntimeError):
    pass


class UnavailableTransport:
    def check_available(self) -> None:
        raise ReaderTransportUnavailable(
            "canonical actual-reader transport is not verified; no attempt was dispatched"
        )

    async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
        self.check_available()


class HandoffUnknown(RuntimeError):
    """The receiver may have started. Never retry this run or regard timeout as stop proof."""


def _consume_late_result(task: asyncio.Task[None]) -> None:
    # A receiver ignoring cancellation may still act. Consume its eventual exception, but never
    # acknowledge it or release quarantine. Only trusted physical stop/reset proof permits reuse.
    if not task.cancelled():
        task.exception()


@dataclass(frozen=True, slots=True)
class ManualRunController:
    database_url: str
    transport: StartTransport = UnavailableTransport()
    acknowledgement_timeout_seconds: float = 10

    async def dispatch(
        self,
        reference: DispatchReference,
        *,
        expected_revision: int,
    ) -> DispatchReference:
        timeout = self.acknowledgement_timeout_seconds
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("handoff acknowledgement timeout must be within (0, 30] seconds")
        self.transport.check_available()
        # This helper owns the transaction: a caller cannot accidentally send before its commit.
        with workspace_connection(self.database_url, reference.workspace_id) as conn:
            # Lease admission locks runner before run. Keep that order here as well.
            conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (reference.runner_id,))
            stored = runs.load_run_for_update(conn, run_id=reference.run_id)
            if stored.state.revision != expected_revision:
                raise runs.StaleRevision("run changed before manual dispatch")
            # Serialize with exact approval revocation; a historical approval response is not
            # dispatch authority. Membership and environment are also freshly checked by the gate.
            conn.execute(
                "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
                "WHERE r.id=%s FOR SHARE OF a",
                (reference.run_id,),
            )
            runners.assert_manual_dispatch_authorized(
                conn,
                workspace_id=reference.workspace_id,
                run_id=reference.run_id,
                runner_id=reference.runner_id,
                lease_id=reference.lease_id,
                epoch=reference.epoch,
            )
            lease = conn.execute(
                "SELECT l.deadline_at FROM desktop_lease l JOIN run_attempt a "
                "ON a.id=l.attempt_id AND a.workspace_id=l.workspace_id "
                "WHERE l.id=%s AND l.attempt_id=%s AND a.run_id=%s AND a.lease_epoch=%s",
                (reference.lease_id, reference.attempt_id, reference.run_id, reference.epoch),
            ).fetchone()
            if lease is None:
                raise runners.DispatchRefused("attempt does not belong to this run and lease epoch")
            timeout = min(timeout, (lease["deadline_at"] - datetime.now(UTC)).total_seconds())
            if timeout <= 0:
                raise runners.DispatchRefused("lease expired before dispatch commitment")
            runs.apply_transition(
                conn,
                run_id=reference.run_id,
                reducer=reducers.progress,
                expected_revision=expected_revision,
                operation_id=str(uuid.uuid4()),
                topic="run.running",
                actor_service="manual-run-controller",
                audit_action="MANUAL_DISPATCH_CLAIMED",
                audit_context=asdict(reference),
            )
            ticket = supervisor_dispatch.issue(conn, **asdict(reference))
        # The durable RUNNING claim is intentionally conservative: execution may have begun. It
        # supplies no observation, assertion, outcome, action permission, or completion evidence.
        task: asyncio.Task[None] | None = None
        try:
            timeout = min(timeout, (lease["deadline_at"] - datetime.now(UTC)).total_seconds())
            timeout = min(
                timeout,
                (
                    parse_rfc3339_utc(ticket.expires_at, field="expiresAt") - datetime.now(UTC)
                ).total_seconds(),
            )
            if timeout <= 0:
                raise TimeoutError("lease expired during dispatch commitment")
            task = asyncio.create_task(self.transport.start(reference, ticket=ticket))
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                raise TimeoutError("manual handoff acknowledgement was not received")
            task.result()
        except BaseException as exc:
            if task is not None:
                task.cancel()
                task.add_done_callback(_consume_late_result)
            # Even caller cancellation is ambiguous after the commitment. If this write fails,
            # the durable RUNNING claim remains non-replayable and requires recovery inspection.
            self.interrupt(reference)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise HandoffUnknown(
                "manual handoff is unknown; run interrupted, desktop quarantined"
            ) from None
        return reference

    def interrupt(self, reference: DispatchReference) -> None:
        """Explicit crash/timeout recovery. Never resend or infer that the desktop has stopped."""
        with workspace_connection(self.database_url, reference.workspace_id) as conn:
            runners.interrupt_manual_handoff(
                conn,
                workspace_id=reference.workspace_id,
                run_id=reference.run_id,
                attempt_id=reference.attempt_id,
                runner_id=reference.runner_id,
                lease_id=reference.lease_id,
                epoch=reference.epoch,
            )
