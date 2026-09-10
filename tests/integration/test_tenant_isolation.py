"""Database-level tenant isolation, tested by talking to PostgreSQL directly.

No API, no middleware, no repository abstraction. These tests issue SQL with a workspace scope
established or not established, because the threat being defended against is a SQL path that
forgets its predicate — a new query, a report, a migration script. Middleware cannot help with any
of those, so the isolation has to hold one level below it.

Requirements: FR-001, FR-014. Invariants: INV-07.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0xA))
WS_B = str(uuid.UUID(int=0xB))
USER_A = str(uuid.UUID(int=0x1A))
USER_B = str(uuid.UUID(int=0x1B))


@pytest.fixture()
def seeded(test_database_url: str) -> Iterator[str]:
    """Two workspaces, each with one member, created through an unscoped connection.

    Seeding deliberately uses the unscoped path, which is the only way to write rows for a
    workspace other than the current one — and the tests below prove that path is unusable for
    reading across tenants.
    """
    # Checked before anything else: if the role bypasses RLS, every assertion below is
    # meaningless and should say so in one sentence rather than sixteen.
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)

    # Each step gets its own closed transaction. Nesting a scoped connection inside the truncating
    # transaction deadlocks: the outer one holds the CASCADE locks while the inner one waits for
    # them. Sequencing is not a style choice here.
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace, app_user, audit_event RESTART IDENTITY CASCADE")

    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        for user, email in ((USER_A, "a@example.test"), (USER_B, "b@example.test")):
            conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, email))

    # Membership is workspace-scoped, so writing it requires the matching scope.
    for ws, user in ((WS_A, USER_A), (WS_B, USER_B)):
        with workspace_connection(test_database_url, ws) as scoped:
            scoped.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, 'OWNER')",
                (ws, user),
            )

    yield test_database_url


# --- the core isolation claim ----------------------------------------------------------------


def test_a_scoped_connection_sees_only_its_own_workspace(seeded: str) -> None:
    with workspace_connection(seeded, WS_A) as conn:
        rows = conn.execute("SELECT workspace_id, user_id FROM workspace_membership").fetchall()
    assert len(rows) == 1, "a scoped connection must not see another workspace's memberships"
    assert str(rows[0]["workspace_id"]) == WS_A
    assert str(rows[0]["user_id"]) == USER_A


def test_a_query_with_no_predicate_at_all_still_cannot_cross_tenants(seeded: str) -> None:
    """The case middleware cannot protect: SQL that simply forgot to filter."""
    with workspace_connection(seeded, WS_B) as conn:
        rows = conn.execute("SELECT * FROM workspace_membership").fetchall()
    assert [str(r["workspace_id"]) for r in rows] == [WS_B]


def test_explicitly_asking_for_another_workspace_returns_nothing(seeded: str) -> None:
    """ID substitution, the exact attack in the SECURITY-PRIVACY threat matrix.

    A valid caller in workspace A names workspace B's id directly. The policy, not the query,
    decides the answer.
    """
    with workspace_connection(seeded, WS_A) as conn:
        rows = conn.execute(
            "SELECT * FROM workspace_membership WHERE workspace_id = %s", (WS_B,)
        ).fetchall()
    assert rows == []


def test_an_unscoped_connection_sees_no_workspace_scoped_rows(seeded: str) -> None:
    """Failing closed: an unset scope means nothing, not everything."""
    with unscoped_connection(seeded) as conn:
        assert conn.execute("SELECT * FROM workspace_membership").fetchall() == []
        assert conn.execute("SELECT * FROM enrollment_credential").fetchall() == []
        assert conn.execute("SELECT * FROM environment_authorization").fetchall() == []
        # Workspace-independent tables remain readable, which is what makes sign-in possible.
        assert len(conn.execute("SELECT * FROM app_user").fetchall()) == 2


@pytest.mark.parametrize("bogus", ["", "not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_a_malformed_or_unknown_scope_yields_nothing(seeded: str, bogus: str) -> None:
    """A malformed scope must not raise and then get swallowed into an unscoped query either."""
    with unscoped_connection(seeded) as conn:
        conn.execute("SELECT set_config('accessforge.workspace_id', %s, true)", (bogus,))
        assert conn.execute("SELECT * FROM workspace_membership").fetchall() == []


def test_writes_cannot_be_directed_at_another_workspace(seeded: str) -> None:
    """WITH CHECK, not just USING: a scoped connection cannot insert into another tenant."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with workspace_connection(seeded, WS_A) as conn:
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, 'OWNER')",
                (WS_B, USER_A),
            )


def test_updates_cannot_reach_another_workspace(seeded: str) -> None:
    with workspace_connection(seeded, WS_A) as conn:
        affected = conn.execute(
            "UPDATE workspace_membership SET role = 'VIEWER' WHERE workspace_id = %s", (WS_B,)
        ).rowcount
    assert affected == 0, "an update must not reach another workspace's rows"

    with workspace_connection(seeded, WS_B) as conn:
        row = conn.execute("SELECT role FROM workspace_membership").fetchone()
    assert row is not None and row["role"] == "OWNER", "the other workspace was modified"


def test_deletes_cannot_reach_another_workspace(seeded: str) -> None:
    with workspace_connection(seeded, WS_A) as conn:
        affected = conn.execute(
            "DELETE FROM workspace_membership WHERE workspace_id = %s", (WS_B,)
        ).rowcount
    assert affected == 0
    with workspace_connection(seeded, WS_B) as conn:
        assert len(conn.execute("SELECT 1 FROM workspace_membership").fetchall()) == 1


def test_row_level_security_is_forced_not_merely_enabled(seeded: str) -> None:
    """The distinction that makes these tests meaningful.

    With RLS merely enabled, the table owner bypasses every policy — and in local development the
    application connects as the owner. Only FORCE makes the owner subject to them, so without this
    the isolation tests above would pass while proving nothing.
    """
    with unscoped_connection(seeded) as conn:
        rows = conn.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class
            WHERE relname IN ('workspace_membership', 'enrollment_credential',
                              'runner_device', 'environment_authorization', 'audit_event')
            ORDER BY relname
            """
        ).fetchall()
    assert len(rows) == 5, f"expected five protected tables, found {[r['relname'] for r in rows]}"
    for row in rows:
        assert row["relrowsecurity"], f"{row['relname']} does not have RLS enabled"
        assert row["relforcerowsecurity"], f"{row['relname']} does not FORCE RLS"


def test_the_isolation_tests_would_notice_if_the_policy_were_dropped(seeded: str) -> None:
    """Self-test: confirm these tests detect an absent policy rather than an empty database.

    The policy is dropped and restored inside a transaction that is rolled back, so the schema is
    unchanged afterwards.
    """
    leaked: list[dict[str, object]] = []
    with unscoped_connection(seeded) as conn:
        try:
            with conn.transaction():
                conn.execute("ALTER TABLE workspace_membership NO FORCE ROW LEVEL SECURITY")
                leaked = conn.execute("SELECT * FROM workspace_membership").fetchall()
                # psycopg aborts a transaction block by raising Rollback, not by a method call.
                raise psycopg.Rollback
        except psycopg.Rollback:
            pass

    # Without FORCE, the owning role sees every workspace — which is exactly what the real
    # configuration prevents.
    assert len(leaked) == 2, (
        "dropping FORCE should expose both workspaces; if it does not, these tests are not "
        "measuring what they claim to"
    )

    # And the schema is back to the enforcing configuration.
    with unscoped_connection(seeded) as conn:
        row = conn.execute(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'workspace_membership'"
        ).fetchone()
    assert row is not None and row["relforcerowsecurity"]


# --- audit isolation (independent review finding 1) -----------------------------------------


def test_an_unpredicated_audit_query_cannot_cross_tenants(seeded: str) -> None:
    """Regression: the audit policy had a NULL-workspace carve-out.

    `workspace_id IS NULL OR workspace_id = current_workspace_id()` made every workspace-independent
    row — a sign-in, carrying a user id and whatever the caller put in `detail` — readable by every
    tenant. The original isolation test sidestepped this by adding `WHERE workspace_id IS NOT NULL`,
    so the suite avoided the bug instead of catching it. This query deliberately has no predicate at
    all, like every other table's isolation test.
    """
    with unscoped_connection(seeded) as conn:
        conn.execute(
            "INSERT INTO global_audit_event (actor_user, action, target_kind, outcome, detail) "
            "VALUES (%s, 'SIGN_IN', 'session', 'ALLOWED', %s)",
            (USER_A, '{"from": "198.51.100.7"}'),
        )
    with workspace_connection(seeded, WS_A) as conn:
        conn.execute(
            "INSERT INTO audit_event (workspace_id, actor_user, action, target_kind, outcome) "
            "VALUES (%s, %s, 'EVIDENCE_READ', 'run', 'ALLOWED')",
            (WS_A, USER_A),
        )

    with workspace_connection(seeded, WS_B) as conn:
        scoped = conn.execute("SELECT * FROM audit_event").fetchall()
        # A tenant connection must see no workspace-independent audit rows whatsoever.
        global_rows = conn.execute("SELECT * FROM global_audit_event").fetchall()

    assert scoped == [], "workspace B must not read workspace A's audit trail"
    assert global_rows == [], "a tenant must not read workspace-independent audit rows"


def test_workspace_independent_audit_rows_remain_readable_to_an_operator(seeded: str) -> None:
    """Allowed-path control: the separate table is still usable for its actual purpose."""
    with unscoped_connection(seeded) as conn:
        conn.execute(
            "INSERT INTO global_audit_event (actor_user, action, target_kind, outcome) "
            "VALUES (%s, 'SIGN_IN', 'session', 'ALLOWED')",
            (USER_A,),
        )
        rows = conn.execute("SELECT action FROM global_audit_event").fetchall()
    assert [r["action"] for r in rows] == ["SIGN_IN"]


def test_the_scoped_audit_table_cannot_hold_a_null_workspace(seeded: str) -> None:
    """The carve-out is gone both structurally and by policy.

    Two independent refusals now apply: the column is NOT NULL, and the policy's WITH CHECK
    requires an exact workspace match. The policy fires first, so the error is
    InsufficientPrivilege rather than NotNullViolation — either is a correct refusal, and the test
    accepts both rather than pinning behaviour to whichever guard PostgreSQL evaluates first.
    """
    with pytest.raises((psycopg.errors.NotNullViolation, psycopg.errors.InsufficientPrivilege)):
        with workspace_connection(seeded, WS_A) as conn:
            conn.execute(
                "INSERT INTO audit_event (workspace_id, action, target_kind, outcome) "
                "VALUES (NULL, 'X', 'y', 'ALLOWED')"
            )

    # And the column constraint is genuinely there, not merely implied by the policy.
    with unscoped_connection(seeded) as conn:
        row = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'audit_event' AND column_name = 'workspace_id'"
        ).fetchone()
    assert row is not None and row["is_nullable"] == "NO"


# --- listing a user's own workspaces (independent review finding 2) --------------------------


def test_a_user_can_list_their_own_workspaces_without_a_workspace_scope(seeded: str) -> None:
    """Regression: there was no primitive for this at all.

    `unscoped_connection`'s docstring claimed it supported "looking up which workspaces a user
    belongs to", but row-level security made that return zero rows — so a post-login workspace
    picker was impossible, and module 18 would have been tempted to weaken the isolation model to
    unblock itself.
    """
    from accessforge_persistence import user_connection

    with workspace_connection(seeded, WS_B) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'VIEWER')",
            (WS_B, USER_A),
        )

    with user_connection(seeded, USER_A) as conn:
        rows = conn.execute(
            "SELECT workspace_id, role FROM workspace_membership ORDER BY workspace_id"
        ).fetchall()
    assert {str(r["workspace_id"]) for r in rows} == {WS_A, WS_B}


def test_listing_own_workspaces_reveals_nobody_elses_membership(seeded: str) -> None:
    """The widened policy must expose only the caller's own rows."""
    from accessforge_persistence import user_connection

    with user_connection(seeded, USER_A) as conn:
        rows = conn.execute("SELECT user_id FROM workspace_membership").fetchall()
    assert {str(r["user_id"]) for r in rows} == {USER_A}, "another user's membership became visible"


def test_a_user_scope_does_not_unlock_other_workspace_scoped_tables(seeded: str) -> None:
    """Identifying a user must not become a general-purpose bypass.

    The widened policy applies to workspace_membership only; enrollment credentials, devices and
    environment authorizations stay workspace-scoped.
    """
    from accessforge_persistence import user_connection

    with user_connection(seeded, USER_A) as conn:
        assert conn.execute("SELECT * FROM enrollment_credential").fetchall() == []
        assert conn.execute("SELECT * FROM runner_device").fetchall() == []
        assert conn.execute("SELECT * FROM environment_authorization").fetchall() == []
        assert conn.execute("SELECT * FROM audit_event").fetchall() == []


# --- membership revocation --------------------------------------------------------------------


def test_revoked_membership_is_retained_but_must_not_grant_access(seeded: str) -> None:
    """Revocation keeps the audit trail, so every authorization query must exclude it explicitly."""
    with workspace_connection(seeded, WS_A) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() WHERE user_id = %s", (USER_A,)
        )
    with workspace_connection(seeded, WS_A) as conn:
        all_rows = conn.execute("SELECT revoked_at FROM workspace_membership").fetchall()
        active = conn.execute(
            "SELECT 1 FROM workspace_membership WHERE revoked_at IS NULL"
        ).fetchall()
    assert len(all_rows) == 1 and all_rows[0]["revoked_at"] is not None
    assert active == [], "a revoked membership must not appear as active"


def test_one_role_per_user_per_workspace(seeded: str) -> None:
    """The composite primary key prevents a second, higher role being added alongside the first."""
    with pytest.raises(psycopg.errors.UniqueViolation):
        with workspace_connection(seeded, WS_A) as conn:
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, 'VIEWER')",
                (WS_A, USER_A),
            )


def test_an_unknown_role_is_rejected_by_the_database(seeded: str) -> None:
    """The role vocabulary is enforced in the schema, not only in Python."""
    with pytest.raises(psycopg.errors.CheckViolation):
        with workspace_connection(seeded, WS_A) as conn:
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, 'SUPERADMIN')",
                (WS_A, USER_B),
            )
