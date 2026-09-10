"""Idempotency reservation and replay.

The contract (CONTRACTS section 9): the same principal, workspace, route and key with the same
canonical body replays the accepted operation; a changed body is a 409.

Two details carry the weight:

* **The database decides, not the application.** Reservation is a single `INSERT ... ON CONFLICT`,
  so two concurrent identical requests resolve to one accepted operation without application
  locking and without a window where both see the key as unused.
* **Replay re-checks read authorization.** A stored result is not a capability. Membership may have
  been revoked since the operation was accepted, and returning cached data to someone who has lost
  access would be a leak that no amount of fresh authorization on the original route prevents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# Bounded retention. Past this window a key may be reused, which is a deliberate trade: unbounded
# retention would grow without limit, and an idempotency key is meant to cover a client's retry
# window, not its lifetime.
IDEMPOTENCY_RETENTION = timedelta(hours=24)


class OperationStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IdempotencyConflict(Exception):
    """The same key was reused with a different canonical body."""


@dataclass(frozen=True, slots=True)
class Reservation:
    operation_id: str
    is_replay: bool
    """False when this call created the operation, True when an earlier identical one did.

    The caller must do the work only when this is False. Acting on a replay is how a retry turns
    into a duplicate effect.
    """
    status: OperationStatus
    result: dict[str, Any] | None


def reserve(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    principal_id: str,
    route: str,
    idempotency_key: str,
    request_digest: str,
    now: datetime | None = None,
) -> Reservation:
    """Reserve an operation for this request, or report the existing one.

    Raises ``IdempotencyConflict`` when the key exists with a different request digest. That is a
    client error worth surfacing rather than smoothing over: the same key meaning two different
    requests means the client's retry logic is wrong, and silently accepting the second would
    produce an effect nobody asked for under a key that claims otherwise.
    """
    moment = now or datetime.now(UTC)
    operation_id = str(uuid.uuid4())

    row = conn.execute(
        """
        INSERT INTO operation
            (id, workspace_id, principal_id, route, idempotency_key, request_digest, status,
             created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'ACCEPTED', %s, %s)
        ON CONFLICT (workspace_id, principal_id, route, idempotency_key) DO NOTHING
        RETURNING id
        """,
        (
            operation_id,
            workspace_id,
            principal_id,
            route,
            idempotency_key,
            request_digest,
            moment,
            moment + IDEMPOTENCY_RETENTION,
        ),
    ).fetchone()

    if row is not None:
        return Reservation(
            operation_id=str(row["id"]),
            is_replay=False,
            status=OperationStatus.ACCEPTED,
            result=None,
        )

    # The insert conflicted, so an operation already exists for this identity.
    existing = conn.execute(
        """
        SELECT id, request_digest, status, result
        FROM operation
        WHERE workspace_id = %s AND principal_id = %s AND route = %s AND idempotency_key = %s
        """,
        (workspace_id, principal_id, route, idempotency_key),
    ).fetchone()

    if existing is None:  # pragma: no cover - only reachable if the row vanished mid-statement
        raise RuntimeError("idempotency reservation conflicted but no operation was found")

    if str(existing["request_digest"]) != request_digest:
        raise IdempotencyConflict(
            f"idempotency key {idempotency_key!r} was already used on {route!r} with a different "
            "request body"
        )

    return Reservation(
        operation_id=str(existing["id"]),
        is_replay=True,
        status=OperationStatus(existing["status"]),
        result=existing["result"],
    )


def complete(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    operation_id: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Record the result of an accepted operation.

    Completion is recorded separately from acceptance because they are different facts. A queue
    acknowledgement means a message was delivered; it does not mean the business operation finished.
    """
    conn.execute(
        """
        UPDATE operation
        SET status = 'COMPLETED', result = %s, completed_at = %s
        WHERE id = %s AND status = 'ACCEPTED'
        """,
        (Jsonb(result), now or datetime.now(UTC), operation_id),
    )


def fail(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    operation_id: str,
    reason: str,
    now: datetime | None = None,
) -> None:
    conn.execute(
        """
        UPDATE operation
        SET status = 'FAILED', result = %s, completed_at = %s
        WHERE id = %s AND status = 'ACCEPTED'
        """,
        (
            Jsonb({"error": reason}),
            now or datetime.now(UTC),
            operation_id,
        ),
    )


def purge_expired(conn: psycopg.Connection[dict[str, Any]], *, now: datetime | None = None) -> int:
    """Delete idempotency records past their retention window.

    Returns the count, so an operator can see that retention is actually running rather than
    assuming it.
    """
    return conn.execute(
        "DELETE FROM operation WHERE expires_at <= %s", (now or datetime.now(UTC),)
    ).rowcount
