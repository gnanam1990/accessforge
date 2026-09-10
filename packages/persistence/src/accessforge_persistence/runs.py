"""Persisting run state through the module 02 reducers.

The reducers are the only authority on which transitions are admissible. This module loads a row,
hands it to a reducer, and writes back what the reducer returned — it never decides a transition
itself. Reimplementing the rules in SQL would create a second definition of the state machine, and
the two would eventually disagree about something that matters.

Three properties are enforced here:

* **Optimistic concurrency.** The write carries the revision it read, so a stale worker's update
  affects zero rows and is reported rather than silently lost.
* **State and outbox commit together.** `apply_transition` writes the run, the audit row and the
  outbox message in one transaction. A message that exists before its state would let a consumer act
  on work that was never accepted.
* **Terminal records are immutable**, enforced by a database trigger as well as by the reducers.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.reducers import RunState, TransitionError
from accessforge_domain.states import Outcome, RunStatus

from .outbox import enqueue_message


class StaleRevision(Exception):
    """The run moved between reading and writing, so this update was refused.

    Raised rather than retried. A caller that read state, made a decision and lost the race needs to
    re-read and decide again; retrying the same write would apply a decision made about a state that
    no longer exists.
    """


class TerminalRun(Exception):
    """The run is terminal and cannot be modified. A retry creates a new linked run."""


@dataclass(frozen=True, slots=True)
class StoredRun:
    state: RunState
    workspace_id: str


def create_run(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    manifest_digest: str,
    project_id: str | None = None,
    authorization_id: str | None = None,
    retry_of: str | None = None,
    run_id: str | None = None,
) -> str:
    """Create a QUEUED run.

    ``retry_of`` links a new run to the terminal one it replaces. A retry is always a new run with a
    new identity and a fresh fixture instance; nothing resumes a terminal record.
    """
    identifier = run_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO run (id, workspace_id, project_id, manifest_digest, authorization_id,
                         status, outcome, retry_of)
        VALUES (%s, %s, %s, %s, %s, 'QUEUED', 'NOT_EVALUATED', %s)
        """,
        (identifier, workspace_id, project_id, manifest_digest, authorization_id, retry_of),
    )
    return identifier


def _row_to_state(row: dict[str, Any]) -> RunState:
    return RunState(
        run_id=str(row["id"]),
        status=RunStatus(row["status"]),
        outcome=Outcome(row["outcome"]),
        revision=int(row["revision"]),
        lease_epoch=int(row["lease_epoch"]),
        execution_began=bool(row["execution_began"]),
        unresolved_action=bool(row["unresolved_action"]),
        cancel_requested_at=(
            row["cancel_requested_at"].isoformat().replace("+00:00", "Z")
            if row["cancel_requested_at"]
            else None
        ),
        cancellation_revision=(
            int(row["cancellation_revision"]) if row["cancellation_revision"] is not None else None
        ),
        stop_acknowledged_at=(
            row["stop_acknowledged_at"].isoformat().replace("+00:00", "Z")
            if row["stop_acknowledged_at"]
            else None
        ),
        stop_acknowledged_epoch=(
            int(row["stop_acknowledged_epoch"])
            if row["stop_acknowledged_epoch"] is not None
            else None
        ),
        ambiguity_reason=row["ambiguity_reason"],
        quarantined=bool(row["quarantined"]),
    )


def load_run(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> StoredRun:
    row = conn.execute("SELECT * FROM run WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        # Indistinguishable from "exists in another workspace", because row-level security has
        # already filtered it. Saying more would confirm the existence of another tenant's run.
        raise LookupError(f"no run {run_id} in this workspace")
    return StoredRun(state=_row_to_state(row), workspace_id=str(row["workspace_id"]))


def load_run_for_update(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> StoredRun:
    """Load a run and hold a row lock for the rest of the transaction.

    Used where a decision must be made on state that cannot change underneath it. Optimistic
    revision checking covers the ordinary case; this is for the sequencer and cancellation paths,
    where a lost race would produce an ambiguous physical situation rather than a retryable error.
    """
    row = conn.execute("SELECT * FROM run WHERE id = %s FOR UPDATE", (run_id,)).fetchone()
    if row is None:
        raise LookupError(f"no run {run_id} in this workspace")
    return StoredRun(state=_row_to_state(row), workspace_id=str(row["workspace_id"]))


def apply_transition(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    reducer: Callable[[RunState], RunState],
    operation_id: str,
    topic: str,
    expected_revision: int | None = None,
    actor_user: str | None = None,
    actor_service: str | None = None,
    audit_action: str | None = None,
    now: datetime | None = None,
) -> RunState:
    """Apply one reducer-admitted transition, atomically with its audit row and outbox message.

    The caller supplies a reducer closure from ``accessforge_domain.reducers``; this function does
    not interpret the transition. If the reducer refuses, nothing is written.

    All three writes land in the caller's transaction. A queue message published before its state
    existed would let a consumer act on work that was never accepted, so the outbox row is created
    here rather than after the commit.

    ``expected_revision`` is the revision the caller last observed. Pass it whenever the decision to
    transition was made outside this call — which is the normal case for a worker that read state,
    did something, and came back. Without it the guard is unreachable: re-reading under the row lock
    and then writing with the revision just read can never mismatch, which made the check look like
    protection while protecting nothing.
    """
    moment = now or datetime.now(UTC)
    stored = load_run_for_update(conn, run_id=run_id)
    before = stored.state

    if expected_revision is not None and expected_revision != before.revision:
        raise StaleRevision(
            f"run {run_id} is at revision {before.revision}, caller expected {expected_revision}; "
            "the decision was made about a state that no longer exists"
        )

    try:
        after = reducer(before)
    except TransitionError as exc:
        if "terminal" in str(exc):
            raise TerminalRun(str(exc)) from exc
        raise

    updated = conn.execute(
        """
        UPDATE run
        SET status = %s, outcome = %s, revision = %s, lease_epoch = %s,
            execution_began = %s, unresolved_action = %s,
            cancel_requested_at = %s, cancellation_revision = %s,
            stop_acknowledged_at = %s, stop_acknowledged_epoch = %s,
            ambiguity_reason = %s, quarantined = %s, updated_at = %s
        WHERE id = %s AND revision = %s
        """,
        (
            after.status.value,
            after.outcome.value,
            after.revision,
            after.lease_epoch,
            after.execution_began,
            after.unresolved_action,
            after.cancel_requested_at,
            after.cancellation_revision,
            after.stop_acknowledged_at,
            after.stop_acknowledged_epoch,
            after.ambiguity_reason,
            after.quarantined,
            moment,
            run_id,
            # The revision the reducer computed from. Belt and braces alongside the row lock: a
            # future caller that reaches the UPDATE without the lock still cannot clobber a
            # concurrent writer.
            before.revision,
        ),
    ).rowcount

    if updated != 1:
        # The row lock above makes this rare, but it is the correct response if it happens: the
        # decision was made about a state that no longer exists.
        raise StaleRevision(
            f"run {run_id} was at revision {before.revision} when the transition was computed, "
            "and is no longer"
        )

    conn.execute(
        """
        INSERT INTO audit_event
            (workspace_id, actor_user, actor_service, action, target_kind, target_id, outcome,
             occurred_at, detail)
        VALUES (%s, %s, %s, %s, 'run', %s, 'ALLOWED', %s, %s)
        """,
        (
            stored.workspace_id,
            actor_user,
            actor_service,
            audit_action or f"RUN_{after.status.value}",
            run_id,
            moment,
            Jsonb(
                {
                    "from": before.status.value,
                    "to": after.status.value,
                    "outcome": after.outcome.value,
                    "revision": after.revision,
                }
            ),
        ),
    )

    enqueue_message(
        conn,
        workspace_id=stored.workspace_id,
        operation_id=operation_id,
        topic=topic,
        # A reference, not a command. The consumer re-reads the run at the revision it finds, so a
        # duplicated or delayed message cannot justify an action the current state does not.
        reference={"runId": run_id, "revision": after.revision, "status": after.status.value},
    )

    return after


def start_attempt(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    workspace_id: str,
    lease_epoch: int,
) -> str:
    """Open an attempt for a lease epoch.

    One attempt per (run, epoch), enforced by a unique constraint: a second attempt at the same
    epoch would mean two actors believing they hold the same desktop session.
    """
    attempt_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO run_attempt (id, workspace_id, run_id, lease_epoch)
        VALUES (%s, %s, %s, %s)
        """,
        (attempt_id, workspace_id, run_id, lease_epoch),
    )
    return attempt_id
