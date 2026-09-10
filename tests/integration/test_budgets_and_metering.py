"""Entitlements, usage metering and budget admission, against real PostgreSQL.

The test that decides whether this module is worth anything is the concurrent one. A limit checked
without a lock is not a limit: two requests arriving together both read the same total, both find
room, and both are admitted. No line of code is wrong, and the workspace is over its limit by
exactly the number of requests that raced.

The rest of the suite is about the three numbers a limit is made of — measured, estimated and
unavailable — and about redelivery, which is the normal case in a system whose only delivery
guarantee is at-least-once.

Requirements: FR-021, FR-025. Invariants: INV-07, INV-08.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x260))
WS_OTHER = str(uuid.UUID(int=0x261))
ADMIN = "admin@example.test"


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "Budgeted"), (WS_OTHER, "Other")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _entitle(db: str, workspace_id: str = WS, **over: int) -> int:
    limits: dict[str, int] = {
        "max_runs_per_day": 5,
        "max_actions_per_day": 100,
        "max_wall_seconds_per_day": 3600,
        "max_model_tokens_per_day": 10_000,
        "max_concurrent_runs": 2,
    }
    limits.update(over)
    with workspace_connection(db, workspace_id) as conn:
        return budgets.configure_entitlement(
            conn,
            workspace_id=workspace_id,
            configured_by=ADMIN,
            reason="initial allowance",
            **limits,
        )


# --------------------------------------------------------------------------------------------------
# Entitlements
# --------------------------------------------------------------------------------------------------


def test_a_workspace_with_no_entitlement_is_refused_rather_than_unlimited(db: str) -> None:
    """The distinction the whole module turns on.

    "Nobody has decided what your allowance is" and "you may do anything" are different states, and
    defaulting to a generous number makes them the same one.
    """
    with workspace_connection(db, WS) as conn:
        with pytest.raises(budgets.NoEntitlement, match="nobody has configured"):
            budgets.admit_within_budget(
                conn,
                workspace_id=WS,
                kind="RUN_ADMITTED",
                quantity=1,
                event_key="run-1",
            )


def test_configuring_appends_a_revision_and_never_edits_one(db: str) -> None:
    first = _entitle(db)
    assert first == 1
    second = _entitle(db, max_runs_per_day=9)
    assert second == 2

    with workspace_connection(db, WS) as conn:
        # A limit that changed in place would make every past admission unexplainable: the row
        # would say what is permitted now and nothing would say what was permitted then.
        with pytest.raises(Exception, match="immutable"):
            conn.execute(
                "UPDATE workspace_entitlement SET max_runs_per_day = 99 WHERE revision = 1"
            )


def test_a_concurrent_reconfiguration_is_refused_rather_than_overwritten(db: str) -> None:
    _entitle(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(budgets.BudgetError, match="Somebody changed it while you were"):
            budgets.configure_entitlement(
                conn,
                workspace_id=WS,
                max_runs_per_day=9,
                max_actions_per_day=100,
                max_wall_seconds_per_day=3600,
                max_model_tokens_per_day=10_000,
                max_concurrent_runs=2,
                configured_by=ADMIN,
                reason="raise",
                expected_revision=0,
            )


def test_the_schema_holds_no_price_currency_or_payment_instrument(db: str) -> None:
    """Structural, and deliberate.

    R1 measures usage and enforces a limit somebody set. A column that could hold a price would grow
    a billing system, and a billing system that grew out of a metering table is one nobody designed.
    """
    with unscoped_connection(db) as conn:
        columns = {
            str(r["column_name"])
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('workspace_entitlement', 'usage_event')"
            ).fetchall()
        }
    forbidden = {"price", "currency", "amount_cents", "cost", "card", "invoice", "payment_method"}
    assert not (columns & forbidden), sorted(columns & forbidden)


# --------------------------------------------------------------------------------------------------
# Metering
# --------------------------------------------------------------------------------------------------


def test_the_same_usage_event_delivered_twice_is_counted_once(db: str) -> None:
    entitlement_revision = _entitle(db)
    assert entitlement_revision == 1

    with workspace_connection(db, WS) as conn:
        assert budgets.record_usage(
            conn, workspace_id=WS, event_key="a", kind="ACTION_DISPATCHED", quantity=4
        )
        # The normal case under at-least-once delivery, not an error.
        assert not budgets.record_usage(
            conn, workspace_id=WS, event_key="a", kind="ACTION_DISPATCHED", quantity=4
        )
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }
    assert totals["ACTION_DISPATCHED"].measured == 4


def test_measured_estimated_and_unavailable_are_reported_separately(db: str) -> None:
    _entitle(db)
    with workspace_connection(db, WS) as conn:
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="m",
            kind="MODEL_TOKENS",
            quantity=100,
            basis="MEASURED",
        )
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="e",
            kind="MODEL_TOKENS",
            quantity=250,
            basis="ESTIMATED",
        )
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="u",
            kind="MODEL_TOKENS",
            quantity=0,
            basis="UNAVAILABLE",
        )
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }

    tokens = totals["MODEL_TOKENS"]
    assert tokens.measured == 100
    assert tokens.estimated == 250
    # A count of events, not a quantity. Reporting it as zero usage would make "we could not
    # measure this" look identical to "nothing happened".
    assert tokens.unavailable_events == 1
    # Both count against the limit: excluding self-reported numbers would let anything a model
    # provider reports be consumed without bound.
    assert tokens.counted_against_limit == 350


def test_a_negative_quantity_is_refused(db: str) -> None:
    _entitle(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(budgets.BudgetError, match="never negative"):
            budgets.record_usage(
                conn, workspace_id=WS, event_key="n", kind="ACTION_DISPATCHED", quantity=-1
            )


def test_usage_outside_the_window_is_not_counted(db: str) -> None:
    _entitle(db)
    now = datetime.now(UTC)
    with workspace_connection(db, WS) as conn:
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="old",
            kind="ACTION_DISPATCHED",
            quantity=90,
            occurred_at=now - timedelta(days=2),
        )
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t
            for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement, now=now)
        }
    assert totals["ACTION_DISPATCHED"].measured == 0


def test_one_workspaces_usage_never_counts_against_another(db: str) -> None:
    _entitle(db)
    _entitle(db, workspace_id=WS_OTHER)
    with workspace_connection(db, WS_OTHER) as conn:
        budgets.record_usage(
            conn, workspace_id=WS_OTHER, event_key="theirs", kind="ACTION_DISPATCHED", quantity=99
        )
    with workspace_connection(db, WS) as conn:
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }
    assert totals["ACTION_DISPATCHED"].measured == 0


# --------------------------------------------------------------------------------------------------
# Admission, and the concurrency that makes it real
# --------------------------------------------------------------------------------------------------


def test_admission_refuses_once_the_limit_is_reached(db: str) -> None:
    _entitle(db, max_runs_per_day=2)
    with workspace_connection(db, WS) as conn:
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="r1"
        )
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="r2"
        )
        with pytest.raises(budgets.BudgetExhausted) as raised:
            budgets.admit_within_budget(
                conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="r3"
            )
    # Which limit, and by how much. "Quota exceeded" tells an operator nothing about what to change.
    assert raised.value.kind == "RUN_ADMITTED"
    assert raised.value.limit == 2
    assert raised.value.used == 2
    assert "not raised automatically" in str(raised.value)


def test_readmitting_the_same_event_does_not_charge_twice(db: str) -> None:
    _entitle(db, max_runs_per_day=1)
    with workspace_connection(db, WS) as conn:
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="same"
        )
        # A retry of an admission already granted. Refusing it would turn a retry into a rejection;
        # admitting it again would charge twice for one decision.
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="same"
        )
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }
    assert totals["RUN_ADMITTED"].measured == 1


def test_concurrent_admissions_cannot_overspend_the_limit(db: str) -> None:
    """The test that decides whether this is a limit or a suggestion.

    Eight requests arrive at once against a limit of three, each on its own connection and its own
    transaction — the arrangement a real API has. Without a lock on the entitlement row every one of
    them reads the same total, finds room, and is admitted; the workspace ends up at eight, and no
    single line of code is wrong.
    """
    _entitle(db, max_runs_per_day=3)

    def attempt(index: int) -> str:
        with workspace_connection(db, WS) as conn:
            try:
                budgets.admit_within_budget(
                    conn,
                    workspace_id=WS,
                    kind="RUN_ADMITTED",
                    quantity=1,
                    event_key=f"concurrent-{index}",
                )
            except budgets.BudgetExhausted:
                return "refused"
            return "admitted"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))

    assert outcomes.count("admitted") == 3, outcomes
    assert outcomes.count("refused") == 5, outcomes

    with workspace_connection(db, WS) as conn:
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }
    # The recorded total matches what was admitted. A count that exceeded the limit would mean the
    # refusals happened after the charge.
    assert totals["RUN_ADMITTED"].measured == 3


def test_a_refused_admission_records_no_usage(db: str) -> None:
    _entitle(db, max_actions_per_day=1)
    with workspace_connection(db, WS) as conn:
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="ACTION_DISPATCHED", quantity=1, event_key="a1"
        )
        with pytest.raises(budgets.BudgetExhausted):
            budgets.admit_within_budget(
                conn, workspace_id=WS, kind="ACTION_DISPATCHED", quantity=1, event_key="a2"
            )
        entitlement = budgets.current_entitlement(conn, workspace_id=WS)
        totals = {
            t.kind: t for t in budgets.usage_since(conn, workspace_id=WS, entitlement=entitlement)
        }
    # A refusal that charged anyway would make the limit tighten every time somebody hit it.
    assert totals["ACTION_DISPATCHED"].measured == 1


def test_each_kind_has_its_own_limit(db: str) -> None:
    _entitle(db, max_runs_per_day=1, max_actions_per_day=50)
    with workspace_connection(db, WS) as conn:
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="RUN_ADMITTED", quantity=1, event_key="r"
        )
        # Exhausting runs must not exhaust actions: a kind that shared a limit with another would
        # make an operator raise the wrong number.
        budgets.admit_within_budget(
            conn, workspace_id=WS, kind="ACTION_DISPATCHED", quantity=40, event_key="a"
        )
