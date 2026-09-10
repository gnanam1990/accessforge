"""The transactional outbox and durable jobs.

The outbox exists because there is no atomic operation spanning a database and a queue. Writing the
message in the same transaction as the state it announces makes the pair atomic, and publishing
afterwards makes delivery *at-least-once* — never exactly-once, which no queue provides.

Everything downstream is built on that honesty:

* A message carries a **reference**, not a command. The consumer re-reads authoritative state, so a
  stale or duplicated message cannot cause an action the current state does not justify.
* Every message carries a stable ``operation_id`` so consumers deduplicate on business identity
  rather than on delivery identity.
* Claims **expire**. A worker that dies mid-claim must not hold a message forever. An expired claim
  is not evidence the work did not happen — only that nobody is currently responsible for it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

CLAIM_LEASE = timedelta(minutes=5)
MAX_JOB_ATTEMPTS = 5


class JobStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    id: int
    workspace_id: str
    operation_id: str
    topic: str
    reference: dict[str, Any]
    attempts: int


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    workspace_id: str
    operation_id: str
    kind: str
    reference: dict[str, Any]
    attempts: int


def enqueue_message(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    operation_id: str,
    topic: str,
    reference: dict[str, Any],
) -> int:
    """Write an outbox message.

    **Must be called inside the same transaction as the state change it announces.** That is the
    entire point of an outbox, and it is a discipline this function cannot enforce on its own — the
    caller holds the transaction. `runs.apply_transition` is the worked example.
    """
    row = conn.execute(
        """
        INSERT INTO outbox_message (workspace_id, operation_id, topic, reference)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (workspace_id, operation_id, topic, Jsonb(reference)),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def claim_messages(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claimed_by: str,
    limit: int = 10,
    now: datetime | None = None,
) -> list[OutboxMessage]:
    """Claim unpublished messages for this worker.

    ``FOR UPDATE SKIP LOCKED`` is what makes concurrent publishers safe: each worker takes a
    disjoint set in one statement, with no coordination and no worker blocked behind another's rows.

    An expired claim is reclaimable. A worker that died holding a claim must not strand the message,
    and reclaiming is safe precisely because delivery is at-least-once by design.
    """
    moment = now or datetime.now(UTC)
    rows = conn.execute(
        """
        WITH claimable AS (
            SELECT id
            FROM outbox_message
            WHERE published_at IS NULL
              AND (claim_expires_at IS NULL OR claim_expires_at <= %(now)s)
            ORDER BY created_at
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE outbox_message m
        SET claimed_at = %(now)s,
            claimed_by = %(worker)s,
            claim_expires_at = %(expires)s,
            attempts = m.attempts + 1
        WHERE m.id IN (SELECT id FROM claimable)
        RETURNING m.id, m.workspace_id, m.operation_id, m.topic, m.reference, m.attempts
        """,
        {
            "now": moment,
            "limit": limit,
            "worker": claimed_by,
            "expires": moment + CLAIM_LEASE,
        },
    ).fetchall()

    return [
        OutboxMessage(
            id=int(r["id"]),
            workspace_id=str(r["workspace_id"]),
            operation_id=str(r["operation_id"]),
            topic=str(r["topic"]),
            reference=dict(r["reference"]),
            attempts=int(r["attempts"]),
        )
        for r in rows
    ]


def mark_published(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    message_id: int,
    now: datetime | None = None,
) -> None:
    """Record that a message reached the transport.

    This records *publication*, not business completion. A published message whose consumer has not
    yet acted is normal, and treating publication as completion is how a system starts reporting
    work it has not done.
    """
    conn.execute(
        "UPDATE outbox_message SET published_at = %s, claim_expires_at = NULL WHERE id = %s",
        (now or datetime.now(UTC), message_id),
    )


def unpublished_count(conn: psycopg.Connection[dict[str, Any]]) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM outbox_message WHERE published_at IS NULL"
    ).fetchone()
    return int(row["n"]) if row else 0


# --- durable jobs -----------------------------------------------------------------------------


def enqueue_job(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    operation_id: str,
    kind: str,
    reference: dict[str, Any],
) -> str:
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO job (id, workspace_id, operation_id, kind, reference, status)
        VALUES (%s, %s, %s, %s, %s, 'PENDING')
        """,
        (job_id, workspace_id, operation_id, kind, Jsonb(reference)),
    )
    return job_id


def claim_jobs(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claimed_by: str,
    limit: int = 1,
    now: datetime | None = None,
) -> list[Job]:
    """Claim pending jobs, including ones whose previous claim has expired."""
    moment = now or datetime.now(UTC)
    rows = conn.execute(
        """
        WITH claimable AS (
            SELECT id
            FROM job
            WHERE (status = 'PENDING')
               OR (status = 'CLAIMED' AND claim_expires_at <= %(now)s)
            ORDER BY created_at
            LIMIT %(limit)s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE job j
        SET status = 'CLAIMED',
            claimed_by = %(worker)s,
            claim_expires_at = %(expires)s,
            attempts = j.attempts + 1,
            updated_at = %(now)s
        WHERE j.id IN (SELECT id FROM claimable)
        RETURNING j.id, j.workspace_id, j.operation_id, j.kind, j.reference, j.attempts
        """,
        {
            "now": moment,
            "limit": limit,
            "worker": claimed_by,
            "expires": moment + CLAIM_LEASE,
        },
    ).fetchall()

    return [
        Job(
            id=str(r["id"]),
            workspace_id=str(r["workspace_id"]),
            operation_id=str(r["operation_id"]),
            kind=str(r["kind"]),
            reference=dict(r["reference"]),
            attempts=int(r["attempts"]),
        )
        for r in rows
    ]


def finish_job(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    job_id: str,
    status: JobStatus,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    if status in (JobStatus.PENDING, JobStatus.CLAIMED):
        raise ValueError(f"{status} is not a finished state")
    conn.execute(
        """
        UPDATE job
        SET status = %s, last_error = %s, claim_expires_at = NULL, updated_at = %s
        WHERE id = %s
        """,
        (status.value, error, now or datetime.now(UTC), job_id),
    )


def release_job(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    job_id: str,
    error: str | None = None,
    now: datetime | None = None,
) -> JobStatus:
    """Return a job for retry, or abandon it once it has been tried too often.

    Abandonment is a visible terminal state rather than an endless retry. A job that keeps failing
    needs a person to look at it; retrying it forever turns a defect into background noise.
    """
    moment = now or datetime.now(UTC)
    row = conn.execute("SELECT attempts FROM job WHERE id = %s", (job_id,)).fetchone()
    if row is None:
        raise LookupError(f"no job {job_id}")

    if int(row["attempts"]) >= MAX_JOB_ATTEMPTS:
        finish_job(conn, job_id=job_id, status=JobStatus.ABANDONED, error=error, now=moment)
        return JobStatus.ABANDONED

    conn.execute(
        """
        UPDATE job
        SET status = 'PENDING', claimed_by = NULL, claim_expires_at = NULL,
            last_error = %s, updated_at = %s
        WHERE id = %s
        """,
        (error, moment, job_id),
    )
    return JobStatus.PENDING
