"""Recovery inspection after a crash.

The rule that shapes this module: an expired claim tells you nobody is currently responsible for a
piece of work. It tells you **nothing** about whether the work happened.

That distinction decides what is safe to retry. A job whose claim lapsed can be re-claimed, because
a job is a database operation and re-reading state makes a repeat harmless. A desktop action whose
result was never recorded cannot be retried at all — a keystroke may already have been delivered,
and the visibility timeout expiring is not evidence to the contrary (INV-09). Those attempts are
surfaced for quarantine, never rescheduled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg


@dataclass(frozen=True, slots=True)
class AbandonedJob:
    job_id: str
    kind: str
    attempts: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class AmbiguousAttempt:
    run_id: str
    status: str
    lease_epoch: int
    reason: str


def stale_claims(
    conn: psycopg.Connection[dict[str, Any]], *, now: datetime | None = None
) -> list[str]:
    """Jobs whose claim has lapsed and which are therefore re-claimable."""
    rows = conn.execute(
        "SELECT id FROM job WHERE status = 'CLAIMED' AND claim_expires_at <= %s",
        (now or datetime.now(UTC),),
    ).fetchall()
    return [str(r["id"]) for r in rows]


def abandoned_jobs(conn: psycopg.Connection[dict[str, Any]]) -> list[AbandonedJob]:
    """Jobs that exhausted their attempts.

    Surfaced rather than retried. A job that keeps failing needs a person; retrying it indefinitely
    turns a defect into background noise.
    """
    rows = conn.execute(
        "SELECT id, kind, attempts, last_error FROM job WHERE status = 'ABANDONED' "
        "ORDER BY updated_at DESC"
    ).fetchall()
    return [
        AbandonedJob(
            job_id=str(r["id"]),
            kind=str(r["kind"]),
            attempts=int(r["attempts"]),
            last_error=r["last_error"],
        )
        for r in rows
    ]


def ambiguous_attempts(conn: psycopg.Connection[dict[str, Any]]) -> list[AmbiguousAttempt]:
    """Runs that cannot be resolved automatically.

    Two situations qualify, and neither may be retried:

    * an unresolved action — an intent was recorded and no result followed, so the operating system
      may or may not have received it;
    * a cancellation request with no current-epoch stop acknowledgement — the desktop was asked to
      stop and has not confirmed, so it may still be acting.

    Both need a human or a quarantine path. Nothing here reschedules them.
    """
    rows = conn.execute(
        """
        SELECT id, status, lease_epoch, unresolved_action, cancel_requested_at,
               stop_acknowledged_at, stop_acknowledged_epoch
        FROM run
        WHERE status IN ('LEASED', 'RUNNING', 'FINALIZING')
          AND (
                unresolved_action
             OR (cancel_requested_at IS NOT NULL
                 AND (stop_acknowledged_at IS NULL OR stop_acknowledged_epoch <> lease_epoch))
          )
        ORDER BY updated_at
        """
    ).fetchall()

    out: list[AmbiguousAttempt] = []
    for r in rows:
        if r["unresolved_action"]:
            reason = (
                "an action intent was recorded with no result; the operating system may have "
                "received it"
            )
        else:
            reason = (
                "cancellation was requested with no stop acknowledgement for the current epoch; "
                "the desktop may still be acting"
            )
        out.append(
            AmbiguousAttempt(
                run_id=str(r["id"]),
                status=str(r["status"]),
                lease_epoch=int(r["lease_epoch"]),
                reason=reason,
            )
        )
    return out
