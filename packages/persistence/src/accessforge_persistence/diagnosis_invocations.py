"""Shared model reservations (legacy table name); uncertain calls are never replayed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg

from . import budgets


class InvocationRefused(ValueError):
    pass


def reserve(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    operation_id: str,
    request_digest: str,
    tokens: int,
    purpose: Literal["DIAGNOSIS", "REPAIR", "NAVIGATOR"] = "DIAGNOSIS",
) -> None:
    if purpose not in {"DIAGNOSIS", "REPAIR", "NAVIGATOR"}:
        raise InvocationRefused("recognized model purpose required")
    for value in (workspace_id, run_id, operation_id):
        if str(UUID(value)) != value:
            raise InvocationRefused("canonical diagnosis invocation identity required")
    maximum = 150000 if purpose == "NAVIGATOR" else 50000
    if type(tokens) is not int or not 1 <= tokens <= maximum:
        raise InvocationRefused("bounded diagnosis token reservation required")
    row = budgets._current_row(conn, workspace_id=workspace_id, lock=True)
    previous = conn.execute(
        "SELECT request_digest,status FROM diagnosis_invocation "
        "WHERE workspace_id=%s AND operation_id=%s",
        (workspace_id, operation_id),
    ).fetchone()
    if previous is not None:
        raise InvocationRefused(
            "diagnosis invocation already reserved; reconcile rather than call again"
        )
    entitlement = budgets.current_entitlement(conn, workspace_id=workspace_id)
    total = next(
        item
        for item in budgets.usage_since(conn, workspace_id=workspace_id, entitlement=entitlement)
        if item.kind == "MODEL_TOKENS"
    )
    # Use the locked admission revision, not a configuration appended midway through the check.
    limit = int(row["max_model_tokens_per_day"])
    if total.counted_against_limit + tokens > limit:
        raise budgets.BudgetExhausted("MODEL_TOKENS", limit, total.counted_against_limit, tokens)
    conn.execute(
        "INSERT INTO diagnosis_invocation"
        "(operation_id,workspace_id,run_id,request_digest,reserved_tokens,purpose) "
        "VALUES(%s,%s,%s,%s,%s,%s)",
        (operation_id, workspace_id, run_id, request_digest, tokens, purpose),
    )


def finish(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    operation_id: str,
    request_digest: str,
    status: Literal["RECORDED", "UNCONFIRMED", "NOT_CALLED"],
    purpose: Literal["DIAGNOSIS", "REPAIR", "NAVIGATOR"] = "DIAGNOSIS",
) -> None:
    row = conn.execute(
        "UPDATE diagnosis_invocation SET status=%s,finished_at=clock_timestamp() "
        "WHERE workspace_id=%s AND operation_id=%s AND request_digest=%s "
        "AND status='STARTED' AND purpose=%s RETURNING run_id",
        (status, workspace_id, operation_id, request_digest, purpose),
    ).fetchone()
    if row is None:
        raise InvocationRefused(
            "diagnosis invocation cannot be completed again or with different scope"
        )
    if status == "NOT_CALLED":
        return
    # Current worker does not independently measure provider consumption. Keep its full budget
    # hold, record UNKNOWN rather than zero use, and never claim a reservation was measured usage.
    budgets.record_usage(
        conn,
        workspace_id=workspace_id,
        run_id=str(row["run_id"]),
        event_key=f"{purpose.lower()}:{operation_id}:usage",
        kind="MODEL_TOKENS",
        quantity=0,
        basis="UNAVAILABLE",
        occurred_at=datetime.now(UTC),
    )
