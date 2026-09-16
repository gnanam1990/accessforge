"""Actual PostgreSQL membership decisions; no live account grants or identity provider calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from accessforge_persistence import migrate, unscoped_connection, workspace_connection
from accessforge_persistence.memberships import MembershipChangeError, change_membership

pytestmark = pytest.mark.integration


@pytest.fixture
def membership_workspace(test_database_url: str) -> tuple[str, str, str]:
    migrate(test_database_url)
    workspace, owner, other = (str(uuid4()) for _ in range(3))
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES (%s,'Membership test')", (workspace,))
        for user in (owner, other):
            conn.execute(
                "INSERT INTO app_user(id,email) VALUES (%s,%s)", (user, f"{user}@example.test")
            )
    with workspace_connection(test_database_url, workspace) as conn:
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,'OWNER')",
            (workspace, owner),
        )
    return workspace, owner, other


def test_membership_revision_revoke_restore_and_authority(
    test_database_url: str, membership_workspace: tuple[str, str, str]
) -> None:
    workspace, owner, other = membership_workspace

    def change(actor: str, target: str, role: str | None, revision: int) -> int:
        with workspace_connection(test_database_url, workspace) as conn:
            result = change_membership(
                conn,
                workspace_id=workspace,
                actor_user_id=actor,
                target_user_id=target,
                role=role,
                expected_revision=revision,
                reason="Explicit test decision",
            )
            return int(result["revision"])

    with pytest.raises(MembershipChangeError, match="last active owner"):
        change(owner, owner, None, 1)
    assert change(owner, other, "REVIEWER", 0) == 1
    with pytest.raises(MembershipChangeError, match="current owner"):
        change(other, owner, "VIEWER", 1)
    assert change(owner, other, "OWNER", 1) == 2
    with pytest.raises(MembershipChangeError, match="revision changed"):
        change(owner, other, None, 1)
    assert change(owner, other, None, 2) == 3
    with pytest.raises(MembershipChangeError, match="current owner"):
        change(other, owner, "VIEWER", 1)
    assert change(owner, other, "VIEWER", 3) == 4
    with workspace_connection(test_database_url, workspace) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE workspace_id=%s "
            "AND action='MEMBERSHIP_ADMINISTER'",
            (workspace,),
        ).fetchone()
        assert row and row["n"] == 4


def test_concurrent_self_demotions_preserve_an_owner(
    test_database_url: str, membership_workspace: tuple[str, str, str]
) -> None:
    workspace, owner, other = membership_workspace
    with workspace_connection(test_database_url, workspace) as conn:
        change_membership(
            conn,
            workspace_id=workspace,
            actor_user_id=owner,
            target_user_id=other,
            role="OWNER",
            expected_revision=0,
            reason="Second owner",
        )
    barrier = Barrier(2)

    def demote(user: str) -> str:
        barrier.wait(timeout=10)
        try:
            with workspace_connection(test_database_url, workspace) as conn:
                conn.execute("SET LOCAL lock_timeout='10s'")
                change_membership(
                    conn,
                    workspace_id=workspace,
                    actor_user_id=user,
                    target_user_id=user,
                    role="VIEWER",
                    expected_revision=1,
                    reason="Concurrent self demotion",
                )
            return "changed"
        except MembershipChangeError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(demote, (owner, other))) == ["changed", "refused"]
    with workspace_connection(test_database_url, workspace) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM workspace_membership WHERE workspace_id=%s "
            "AND role='OWNER' AND revoked_at IS NULL",
            (workspace,),
        ).fetchone()
        assert row and row["n"] == 1
