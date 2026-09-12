"""How fast one principal, and one workspace, may write.

Module 26 left this open and `STATUS.md` recorded it: entitlements bound *how much* a workspace may
consume over a billing period, and nothing bounded *how fast* anybody could ask. Those are different
controls and the difference matters -- a caller can exhaust a month's budget in a second, and a
caller with no budget left can still cost a database connection per request.

Three decisions worth reading before changing this.

**The bucket lives in PostgreSQL.** An in-process counter is correct only while there is exactly one
API process. Two behind a load balancer each enforce their own half, so the effective limit rises as
the deployment scales -- a limit that loosens when you add capacity is not a limit.

**One statement decides.** The refill, the comparison and the decrement are a single
`INSERT ... ON CONFLICT DO UPDATE ... WHERE`, so two concurrent requests for the same bucket
serialize on the row and exactly one of them gets the last token. Read-then-write would let both
read
the same count, both find room, and both proceed -- which is how a rate limiter becomes a suggestion
under the load it exists for.

**Time is a parameter, never `now()`.** Passed in so tests can place a request at an exact instant
rather than sleeping, and so the refusal and the retry-after are computed from the same moment. The
elapsed term is clamped at zero: a clock that steps backwards must not mint tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg


class RateLimitError(Exception):
    """A limit could not be evaluated. Never raised to report that a caller is over it."""


#: The refill arithmetic, written once. It appears in the update, in the update's predicate and in
#: the
#: retry-after read, and the three must agree exactly -- a retry-after computed from different
#: arithmetic than the refusal is advice that does not match the rule.
_AVAILABLE = (
    "LEAST(%(capacity)s::double precision, b.tokens + "
    "      GREATEST(0, EXTRACT(EPOCH FROM (%(now)s::timestamptz - b.refilled_at))) * %(rate)s)"
)


@dataclass(frozen=True, slots=True)
class Decision:
    """What the limiter decided, and what to tell the caller.

    `retry_after_seconds` is zero when allowed. When refused it is the whole number of seconds after
    which one token certainly exists -- rounded up, because advice to retry a moment too early is
    advice to be refused twice.
    """

    allowed: bool
    scope_kind: str
    remaining: int
    retry_after_seconds: int

    limit_per_minute: int
    """The sustained rate: how many writes a minute this scope may keep up indefinitely."""

    burst_capacity: int
    """How many may arrive at once before any refill. Separate from the rate, and not the same
    number whenever the burst multiplier is above one.

    Reporting the capacity as the per-minute limit is how a 120/minute policy with a burst of two
    told callers their limit was 240 -- a number they could not sustain and which appeared in
    nothing an operator had configured."""

    @property
    def meaning(self) -> str:
        allowance = f"{self.limit_per_minute} writes per minute"
        if self.burst_capacity != self.limit_per_minute:
            allowance += f" with a burst of {self.burst_capacity}"
        if self.allowed:
            return (
                f"within the {self.scope_kind.lower()} allowance of {allowance}; "
                f"{self.remaining} remaining at this instant"
            )
        return (
            f"over the {self.scope_kind.lower()} allowance of {allowance}. This is a rate, not a "
            "quota: nothing has been consumed and the same request will be admitted after "
            f"{self.retry_after_seconds}s. Retrying sooner is refused again."
        )


def consume(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    scope_kind: str,
    scope_id: str,
    capacity: int,
    refill_per_second: float,
    now: datetime,
) -> Decision:
    """Take one token from a bucket, or report how long until there is one.

    `capacity` is the burst a caller may spend at once; `refill_per_second` is the sustained rate.
    Together they are the limit: a caller may arrive `capacity` times immediately and then once per
    `1/refill_per_second` seconds.

    Refusal is not an error and does not raise. It is a decision the caller is entitled to act on,
    and raising would make every call site handle control flow through an exception for the most
    ordinary outcome a limiter has.
    """
    if scope_kind not in {"PRINCIPAL", "WORKSPACE"}:
        raise RateLimitError(f"unknown rate-limit scope {scope_kind!r}")
    if capacity < 1:
        raise RateLimitError(
            f"a capacity of {capacity} admits nothing at all. A limit that refuses every request "
            "is a configuration mistake, not a strict limit, so it is refused here rather than "
            "taking the whole API offline quietly."
        )
    if refill_per_second <= 0:
        raise RateLimitError(
            f"a refill rate of {refill_per_second} never returns a token, so the first burst would "
            "be the last requests this scope ever made."
        )

    # The sustained rate, recovered from the refill rather than accepted as a second parameter that
    # could drift from it. `refill_per_second` is the rate, so this is the rate -- there is no way
    # for the reported limit and the enforced limit to be different numbers.
    limit_per_minute = int(round(refill_per_second * 60))

    params = {
        "kind": scope_kind,
        "id": scope_id,
        "workspace": scope_id if scope_kind == "WORKSPACE" else None,
        "capacity": capacity,
        "rate": refill_per_second,
        "now": now,
    }
    row = conn.execute(
        f"""
        INSERT INTO rate_limit_bucket AS b (scope_kind, scope_id, workspace_id, tokens, refilled_at)
        VALUES (%(kind)s, %(id)s, %(workspace)s, %(capacity)s::double precision - 1, %(now)s)
        ON CONFLICT (scope_kind, scope_id) DO UPDATE
           SET tokens = {_AVAILABLE} - 1,
               refilled_at = %(now)s
         WHERE {_AVAILABLE} >= 1
        RETURNING tokens
        """,  # noqa: S608 - no interpolated value is caller data; see _AVAILABLE
        params,
    ).fetchone()

    if row is not None:
        return Decision(
            allowed=True,
            scope_kind=scope_kind,
            remaining=int(row["tokens"]),
            retry_after_seconds=0,
            limit_per_minute=limit_per_minute,
            burst_capacity=capacity,
        )

    # Refused. The same arithmetic, read rather than written, so the advice matches the rule that
    # produced it. A separate statement is safe here: the row exists and nothing is being changed,
    # and a concurrent refill can only make the real wait shorter than the number reported.
    available = conn.execute(
        f"SELECT {_AVAILABLE} AS available FROM rate_limit_bucket AS b "  # noqa: S608
        " WHERE b.scope_kind = %(kind)s AND b.scope_id = %(id)s",
        params,
    ).fetchone()
    if available is None:
        # The row vanished between the two statements, which means a prune ran. Nothing is owed.
        raise RateLimitError(
            f"the {scope_kind} bucket for {scope_id} disappeared while it was being evaluated"
        )
    shortfall = max(0.0, 1.0 - float(available["available"]))
    # Ceiling, and never below one second: advising a caller to retry in zero seconds invites the
    # tight loop a limiter exists to stop.
    wait = max(1, int(-(-shortfall // refill_per_second)) if refill_per_second else 1)
    return Decision(
        allowed=False,
        scope_kind=scope_kind,
        remaining=0,
        retry_after_seconds=wait,
        limit_per_minute=limit_per_minute,
        burst_capacity=capacity,
    )


def prune_idle_buckets(
    conn: psycopg.Connection[dict[str, Any]], *, idle_for: timedelta, now: datetime
) -> int:
    """Delete buckets that have been full and untouched for longer than `idle_for`.

    One row per principal and per workspace that has ever written is a table that only grows, and
    the
    rows are pure derived state: a deleted bucket is recreated full by the next request, which is
    exactly what an idle bucket already was. Returns how many went, for an operator who wants to see
    the sweep doing something.

    `idle_for` must comfortably exceed the time a bucket takes to refill completely, or pruning
    would
    hand back a full allowance to a caller who is mid-refill. The caller passes it; the default in
    the
    worker is an hour against a per-minute limit.
    """
    if idle_for <= timedelta(0):
        raise RateLimitError(
            "pruning buckets that are zero seconds idle would delete the bucket of a request in "
            "flight, handing its caller a fresh allowance on every attempt"
        )
    return conn.execute(
        "DELETE FROM rate_limit_bucket WHERE refilled_at < %s", (now - idle_for,)
    ).rowcount


def bucket_state(
    conn: psycopg.Connection[dict[str, Any]], *, scope_kind: str, scope_id: str
) -> dict[str, Any] | None:
    """The raw row, for tests and for an operator explaining a refusal. None if there is none."""
    return conn.execute(
        "SELECT scope_kind, scope_id, workspace_id, tokens, refilled_at FROM rate_limit_bucket "
        " WHERE scope_kind = %s AND scope_id = %s",
        (scope_kind, scope_id),
    ).fetchone()
