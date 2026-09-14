"""Trusted baseline callback to the existing one-shot native dispatch controller.

No transport is enabled by default. An acknowledgement is delivery, not STOP or reader evidence.
The caller must keep the protected baseline callback alive until the exact reader has stopped.
"""

from __future__ import annotations

import math

from accessforge_build_worker.baseline_session import BaselineSession
from accessforge_persistence import baseline_runs, workspace_connection

from .manual_dispatch import (
    DispatchReference,
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
