"""Trusted baseline callback to the existing one-shot native dispatch controller.

No transport is enabled by default. An acknowledgement is delivery, not STOP or reader evidence.
The caller must keep the protected baseline callback alive until the exact reader has stopped.
"""

from __future__ import annotations

import asyncio
import math

from accessforge_build_worker.baseline_session import BaselineSession
from accessforge_persistence import baseline_runs, workspace_connection

from .manual_dispatch import (
    DispatchReference,
    HandoffUnknown,
    ManualRunController,
    StartTransport,
    UnavailableTransport,
)


async def admit_and_dispatch_reader(
    session: BaselineSession,
    *,
    runner_id: str,
    attempt_id: str,
    transport: StartTransport | None = None,
    acknowledgement_timeout_seconds: float = 10,
) -> DispatchReference:
    """Commit original reader admission, reconstruct exact identity, then deliver once.

    Transport qualification is checked before reserving the only reader lease. The existing
    controller rechecks availability and live authority before committing dispatch. Failure after
    admission is not permission to retry, replace the lease or release the protected runtime:
    preserve the original attempt and use explicit recovery. No exceptions are retried here.
    """
    if (
        not math.isfinite(acknowledgement_timeout_seconds)
        or not 0 < acknowledgement_timeout_seconds <= 30
    ):
        raise ValueError("handoff acknowledgement timeout must be within (0, 30] seconds")
    if transport is None:
        transport = UnavailableTransport()
    transport.check_available()
    lease = session.admit_reader(runner_id=runner_id, attempt_id=attempt_id)
    with workspace_connection(session.database_url, session.workspace_id) as conn:
        row = conn.execute(
            "SELECT r.id AS run_id,r.revision,l.attempt_id,l.runner_id,l.id AS lease_id,l.epoch "
            "FROM baseline_regression_attempt a JOIN run r "
            "ON r.id=a.run_id AND r.workspace_id=a.workspace_id "
            "JOIN baseline_reader_lease b ON b.run_id=r.id AND b.workspace_id=r.workspace_id "
            "JOIN desktop_lease l ON l.id=b.lease_id AND l.workspace_id=b.workspace_id "
            "AND l.run_id=r.id AND l.epoch=b.lease_epoch "
            "WHERE a.id=%s AND a.workspace_id=%s AND l.id=%s "
            "AND l.runner_id=%s AND l.attempt_id=%s AND l.epoch=%s",
            (
                session.claim.attempt_id,
                session.workspace_id,
                lease.lease_id,
                runner_id,
                attempt_id,
                lease.epoch,
            ),
        ).fetchone()
        if row is None:
            raise baseline_runs.Refused("original admitted baseline reader binding unavailable")
        reference = DispatchReference(
            workspace_id=session.workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            runner_id=str(row["runner_id"]),
            lease_id=str(row["lease_id"]),
            epoch=int(row["epoch"]),
        )
        revision = int(row["revision"])
    return await ManualRunController(
        session.database_url, transport, acknowledgement_timeout_seconds
    ).dispatch(reference, expected_revision=revision)


def _reader_stopped(session: BaselineSession, reference: DispatchReference) -> bool:
    """Positive original-lease STOP evidence; absence is never successful cleanup."""
    with workspace_connection(session.database_url, session.workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='2s'")
        row = conn.execute(
            "SELECT l.released_at IS NOT NULL AND l.stop_acknowledged_at IS NOT NULL "
            "AND l.stop_acknowledged_epoch=l.epoch "
            "AND l.release_reason='STOP_ACKNOWLEDGED' AS stopped "
            "FROM baseline_session_binding s JOIN baseline_reader_lease b "
            "USING(run_id,workspace_id) JOIN desktop_lease l "
            "ON l.id=b.lease_id AND l.workspace_id=b.workspace_id "
            "AND l.run_id=b.run_id AND l.epoch=b.lease_epoch "
            "WHERE s.regression_attempt_id=%s AND s.workspace_id=%s AND s.run_id=%s "
            "AND l.id=%s AND l.attempt_id=%s AND l.runner_id=%s AND l.epoch=%s",
            (
                session.claim.attempt_id,
                reference.workspace_id,
                reference.run_id,
                reference.lease_id,
                reference.attempt_id,
                reference.runner_id,
                reference.epoch,
            ),
        ).fetchone()
        if row is None:
            raise baseline_runs.Refused("original dispatched baseline reader disappeared")
        return row["stopped"] is True


async def admit_dispatch_and_wait_reader(
    session: BaselineSession,
    *,
    runner_id: str,
    attempt_id: str,
    transport: StartTransport | None = None,
    timeout_seconds: float = 60,
) -> DispatchReference:
    """Keep the baseline callback alive through original STOP, not merely delivery ACK.

    The total bound includes dispatch. Timeout/cancellation never releases a lease, retries
    delivery or claims STOP. Protected runtime closure and evidence finalization still follow
    outside this callback; a STOP receipt is neither an evaluation nor physical qualification.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 60
    ):
        raise ValueError("baseline reader wait must be within (0, 60] seconds")
    try:
        async with asyncio.timeout(timeout_seconds):
            reference = await admit_and_dispatch_reader(
                session,
                runner_id=runner_id,
                attempt_id=attempt_id,
                transport=transport,
                acknowledgement_timeout_seconds=min(10, timeout_seconds),
            )
            while not await asyncio.to_thread(_reader_stopped, session, reference):
                await asyncio.sleep(0.1)
            return reference
    except TimeoutError:
        raise HandoffUnknown(
            "baseline reader STOP unconfirmed; reconcile original attempt, never redispatch"
        ) from None
