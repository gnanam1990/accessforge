"""Privacy-safe operational metrics.

Counts and durations only. Nothing here returns a workspace name, a user, a payload or a reader
transcript, because an operations dashboard is exactly the kind of surface that accumulates data
nobody decided to collect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg


@dataclass(frozen=True, slots=True)
class QueueHealth:
    unpublished_messages: int
    oldest_unpublished_seconds: float | None
    pending_jobs: int
    claimed_jobs: int
    stale_claims: int
    abandoned_jobs: int
    ambiguous_attempts: int
    quarantined_runs: int


def queue_health(
    conn: psycopg.Connection[dict[str, Any]], *, now: datetime | None = None
) -> QueueHealth:
    """A numbers-only snapshot of durable-work health."""
    moment = now or datetime.now(UTC)
    row = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM outbox_message WHERE published_at IS NULL) AS unpublished,
          (SELECT extract(epoch FROM %(now)s - min(created_at))
             FROM outbox_message WHERE published_at IS NULL) AS oldest_seconds,
          (SELECT count(*) FROM job WHERE status = 'PENDING')   AS pending,
          (SELECT count(*) FROM job WHERE status = 'CLAIMED')   AS claimed,
          (SELECT count(*) FROM job
             WHERE status = 'CLAIMED' AND claim_expires_at <= %(now)s) AS stale,
          (SELECT count(*) FROM job WHERE status = 'ABANDONED') AS abandoned,
          (SELECT count(*) FROM run
             WHERE status IN ('LEASED','RUNNING','FINALIZING')
               AND (unresolved_action
                    OR (cancel_requested_at IS NOT NULL
                        AND (stop_acknowledged_at IS NULL
                             OR stop_acknowledged_epoch <> lease_epoch)))) AS ambiguous,
          (SELECT count(*) FROM run WHERE quarantined) AS quarantined
        """,
        {"now": moment},
    ).fetchone()
    assert row is not None
    return QueueHealth(
        unpublished_messages=int(row["unpublished"]),
        oldest_unpublished_seconds=(
            float(row["oldest_seconds"]) if row["oldest_seconds"] is not None else None
        ),
        pending_jobs=int(row["pending"]),
        claimed_jobs=int(row["claimed"]),
        stale_claims=int(row["stale"]),
        abandoned_jobs=int(row["abandoned"]),
        ambiguous_attempts=int(row["ambiguous"]),
        quarantined_runs=int(row["quarantined"]),
    )
