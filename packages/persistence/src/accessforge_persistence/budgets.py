"""Entitlements, usage metering and durable budget admission.

The module exists because a limit that is checked but not enforced under concurrency is not a limit.
Three properties carry it.

**Admission takes a lock on the entitlement row.** Two runs requested at the same instant would
otherwise both read the same total, both find room, and both be admitted — and the workspace ends up
over its limit by exactly the number of concurrent requests. `SELECT … FOR UPDATE` serialises the
decision against the row the decision is about, so the second request reads the first one's usage.

**Usage events are idempotent.** Delivery repeats; consumption does not. The unique key is the
producer's own identifier for the event, and a redelivery is a no-op rather than a second charge.

**Measured, estimated and unavailable are different numbers.** A model provider reporting its own
token count is `ESTIMATED` — it is not the sole trusted input to a limit that costs somebody money,
and an operator deciding whether to raise a limit needs to know which kind of number they are
looking at. `UNAVAILABLE` carries a quantity of zero without that meaning nothing happened.

There is no price, no currency and no payment instrument anywhere in this module. R1 measures usage
and enforces a limit an administrator set; it collects no money and computes no saving.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import psycopg

#: The usage kinds a limit is expressed in. One per entitlement column, deliberately: a kind with no
#: limit would be metered and unbounded, which is the shape of a bill nobody agreed to.
UsageKind = Literal["RUN_ADMITTED", "ACTION_DISPATCHED", "WALL_SECONDS", "MODEL_TOKENS"]
Basis = Literal["MEASURED", "ESTIMATED", "UNAVAILABLE"]

#: The window every daily limit is measured over. A rolling window rather than a calendar day: a
#: calendar boundary lets a workspace spend two days' allowance in the minutes either side of
#: midnight, and whose midnight it is has to be decided for a system with no single timezone.
WINDOW = timedelta(days=1)


class BudgetError(RuntimeError):
    """A budget operation was refused."""


class NoEntitlement(BudgetError):
    """This workspace has no configured entitlement.

    A distinct type because the correct answer is different: not "you have spent your allowance" but
    "nobody has decided what your allowance is". Defaulting to a generous number here would mean a
    workspace nobody configured is a workspace with no limit.
    """


class BudgetExhausted(BudgetError):
    """The request would exceed a configured limit. Carries which one, and by how much."""

    def __init__(self, kind: str, limit: int, used: int, requested: int) -> None:
        self.kind = kind
        self.limit = limit
        self.used = used
        self.requested = requested
        super().__init__(
            f"this workspace is limited to {limit} {kind} per day and has used {used}; admitting "
            f"{requested} more would exceed it. The limit is configured by an administrator and is "
            "not raised automatically."
        )


@dataclass(frozen=True, slots=True)
class Entitlement:
    revision: int
    max_runs_per_day: int
    max_actions_per_day: int
    max_wall_seconds_per_day: int
    max_model_tokens_per_day: int
    max_concurrent_runs: int
    configured_by: str
    reason: str

    def limit_for(self, kind: UsageKind) -> int:
        return {
            "RUN_ADMITTED": self.max_runs_per_day,
            "ACTION_DISPATCHED": self.max_actions_per_day,
            "WALL_SECONDS": self.max_wall_seconds_per_day,
            "MODEL_TOKENS": self.max_model_tokens_per_day,
        }[kind]


def configure_entitlement(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    max_runs_per_day: int,
    max_actions_per_day: int,
    max_wall_seconds_per_day: int,
    max_model_tokens_per_day: int,
    max_concurrent_runs: int,
    configured_by: str,
    reason: str,
    expected_revision: int | None = None,
) -> int:
    """Append a new entitlement revision. Returns the revision number.

    `expected_revision` is the revision the caller last read. Supplying it turns a concurrent
    reconfiguration into a refusal rather than a silent overwrite of somebody else's decision — and
    two administrators raising a limit at the same moment is exactly when that matters.
    """
    current = conn.execute(
        "SELECT coalesce(max(revision), 0) AS revision FROM workspace_entitlement "
        "WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    latest = int(current["revision"]) if current else 0

    if expected_revision is not None and expected_revision != latest:
        raise BudgetError(
            f"the entitlement was at revision {latest} and you read revision {expected_revision}. "
            "Somebody changed it while you were deciding; read it again before replacing their "
            "decision with yours."
        )

    revision = latest + 1
    conn.execute(
        """
        INSERT INTO workspace_entitlement
            (id, workspace_id, revision, max_runs_per_day, max_actions_per_day,
             max_wall_seconds_per_day, max_model_tokens_per_day, max_concurrent_runs,
             configured_by, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            revision,
            max_runs_per_day,
            max_actions_per_day,
            max_wall_seconds_per_day,
            max_model_tokens_per_day,
            max_concurrent_runs,
            configured_by,
            reason,
        ),
    )
    return revision


def _current_row(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, lock: bool
) -> dict[str, Any]:
    # Two complete statements rather than one assembled from a flag. Concatenating SQL is how a
    # parameter eventually gets concatenated too, and the linter is right to say so even when the
    # appended text is a literal.
    if lock:
        row = conn.execute(
            "SELECT * FROM workspace_entitlement WHERE workspace_id = %s "
            "ORDER BY revision DESC LIMIT 1 FOR UPDATE",
            (workspace_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workspace_entitlement WHERE workspace_id = %s "
            "ORDER BY revision DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    if row is None:
        raise NoEntitlement(
            "no entitlement is configured for this workspace, so there is no limit to admit "
            "against. This is not an unlimited workspace: it is one nobody has configured, and "
            "defaulting to a generous number here would make those the same thing."
        )
    return dict(row)


def current_entitlement(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str
) -> Entitlement:
    row = _current_row(conn, workspace_id=workspace_id, lock=False)
    return Entitlement(
        revision=int(row["revision"]),
        max_runs_per_day=int(row["max_runs_per_day"]),
        max_actions_per_day=int(row["max_actions_per_day"]),
        max_wall_seconds_per_day=int(row["max_wall_seconds_per_day"]),
        max_model_tokens_per_day=int(row["max_model_tokens_per_day"]),
        max_concurrent_runs=int(row["max_concurrent_runs"]),
        configured_by=str(row["configured_by"]),
        reason=str(row["reason"]),
    )


def record_usage(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    event_key: str,
    kind: UsageKind,
    quantity: int,
    basis: Basis = "MEASURED",
    run_id: str | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """Record consumption. Returns whether this call inserted anything.

    `False` means the event was already recorded — a redelivery, which is the normal case in a
    system whose only delivery guarantee is at-least-once. The return value exists so a caller can
    tell "counted" from "counted already", and neither from "failed".
    """
    if quantity < 0:
        raise BudgetError(
            "usage quantities are never negative. A correction that looked like a measurement "
            "would let a total be talked downwards by whoever produces the events."
        )
    result = conn.execute(
        """
        INSERT INTO usage_event
            (id, workspace_id, event_key, kind, quantity, basis, run_id, occurred_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id, event_key) DO NOTHING
        RETURNING id
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            event_key,
            kind,
            quantity,
            basis,
            run_id,
            occurred_at or datetime.now(UTC),
        ),
    ).fetchone()
    return result is not None


@dataclass(frozen=True, slots=True)
class UsageTotal:
    kind: str
    measured: int
    estimated: int
    unavailable_events: int
    limit: int

    @property
    def counted_against_limit(self) -> int:
        """What admission compares to the limit.

        Measured and estimated together. Excluding estimated would let anything self-reported be
        consumed without bound, which is precisely the number a model provider supplies.
        """
        return self.measured + self.estimated


def usage_since(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    entitlement: Entitlement,
    now: datetime | None = None,
) -> list[UsageTotal]:
    """Usage over the rolling window, split by how each number was obtained."""
    moment = now or datetime.now(UTC)
    rows = conn.execute(
        """
        SELECT kind,
               coalesce(sum(quantity) FILTER (WHERE basis = 'MEASURED'), 0)  AS measured,
               coalesce(sum(quantity) FILTER (WHERE basis = 'ESTIMATED'), 0) AS estimated,
               count(*) FILTER (WHERE basis = 'UNAVAILABLE')                 AS unavailable
        FROM usage_event
        WHERE workspace_id = %s AND occurred_at > %s
        GROUP BY kind
        """,
        (workspace_id, moment - WINDOW),
    ).fetchall()
    by_kind = {str(r["kind"]): r for r in rows}

    totals: list[UsageTotal] = []
    for kind in ("RUN_ADMITTED", "ACTION_DISPATCHED", "WALL_SECONDS", "MODEL_TOKENS"):
        row = by_kind.get(kind)
        totals.append(
            UsageTotal(
                kind=kind,
                measured=int(row["measured"]) if row else 0,
                estimated=int(row["estimated"]) if row else 0,
                # A count of events, not a quantity: the quantity is zero by definition, and
                # reporting it as zero usage would make "we could not measure this" look like
                # "nothing happened".
                unavailable_events=int(row["unavailable"]) if row else 0,
                limit=entitlement.limit_for(kind),
            )
        )
    return totals


def admit_within_budget(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    kind: UsageKind,
    quantity: int,
    event_key: str,
    run_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """Admit consumption against the current entitlement, or refuse.

    The entitlement row is locked first. Everything after that — reading the window's usage,
    comparing it to the limit, recording the new event — happens while no other admission for this
    workspace can be in flight. Without the lock two concurrent requests both read the same total,
    both find room, and both are admitted; the workspace ends up over its limit by exactly the
    number of requests that raced, and no single line of code is wrong.

    Recording happens in the same transaction as the check. A check that committed separately from
    its consumption would leave a window in which the admission is decided and not yet counted.
    """
    moment = now or datetime.now(UTC)
    row = _current_row(conn, workspace_id=workspace_id, lock=True)
    entitlement = Entitlement(
        revision=int(row["revision"]),
        max_runs_per_day=int(row["max_runs_per_day"]),
        max_actions_per_day=int(row["max_actions_per_day"]),
        max_wall_seconds_per_day=int(row["max_wall_seconds_per_day"]),
        max_model_tokens_per_day=int(row["max_model_tokens_per_day"]),
        max_concurrent_runs=int(row["max_concurrent_runs"]),
        configured_by=str(row["configured_by"]),
        reason=str(row["reason"]),
    )

    already = conn.execute(
        "SELECT 1 FROM usage_event WHERE workspace_id = %s AND event_key = %s",
        (workspace_id, event_key),
    ).fetchone()
    if already is not None:
        # A redelivery of an admission already granted. Refusing it here would turn a retry into a
        # rejection, and admitting it again would charge twice for one decision.
        return

    totals = {
        t.kind: t
        for t in usage_since(conn, workspace_id=workspace_id, entitlement=entitlement, now=moment)
    }
    total = totals[kind]
    if total.counted_against_limit + quantity > total.limit:
        raise BudgetExhausted(
            kind=kind,
            limit=total.limit,
            used=total.counted_against_limit,
            requested=quantity,
        )

    record_usage(
        conn,
        workspace_id=workspace_id,
        event_key=event_key,
        kind=kind,
        quantity=quantity,
        basis="MEASURED",
        run_id=run_id,
        occurred_at=moment,
    )


def concurrent_runs(conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str) -> int:
    """Runs currently occupying capacity. Counted from the run table, never from a counter.

    A maintained counter drifts the first time a process dies between decrementing and committing,
    and the drift is silent and permanent.
    """
    row = conn.execute(
        "SELECT count(*) AS n FROM run WHERE status IN ('QUEUED','LEASED','RUNNING','FINALIZING')"
    ).fetchone()
    return int(row["n"]) if row else 0
