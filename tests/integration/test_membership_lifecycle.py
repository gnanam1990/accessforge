"""Actual PostgreSQL membership decisions; no live account grants or identity provider calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
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


def test_membership_http_revision_readback_and_denial_audit(
    test_database_url: str, membership_workspace: tuple[str, str, str]
) -> None:
    workspace, owner, other = membership_workspace
    with workspace_connection(test_database_url, workspace) as conn:
        change_membership(
            conn,
            workspace_id=workspace,
            actor_user_id=owner,
            target_user_id=other,
            role="VIEWER",
            expected_revision=0,
            reason="Existing test relationship",
        )
        session = issue_session(conn, user_id=owner)
    settings = ApiSettings(
        database_url=test_database_url,
        evidence_endpoint_url="http://127.0.0.1:9000",
        evidence_bucket="unused",
        evidence_access_key="unused",
        evidence_secret_key="unused",
        environment="test",
    )
    path = f"/v1/workspaces/{workspace}/members/{other}"
    with TestClient(create_app(settings)) as client:
        client.cookies.set(SESSION_COOKIE, session.session_token)
        headers = {CSRF_HEADER: session.csrf_token, "If-Match": '"1"'}
        current = client.get(path)
        assert current.status_code == 200
        assert current.headers["etag"] == '"1"'
        body = {"role": "MAINTAINER", "reason": "Reviewed test decision"}
        assert client.put(path, json=body, headers={"If-Match": '"1"'}).status_code == 403
        missing = client.put(path, json=body, headers={CSRF_HEADER: session.csrf_token})
        assert missing.json()["code"] == "IF_MATCH_REQUIRED"
        changed = client.put(path, json=body, headers=headers)
        assert changed.status_code == 200, changed.text
        assert changed.json()["revision"] == 2
        assert changed.headers["cache-control"] == "no-store"
        assert client.put(path, json=body, headers=headers).json()["code"] == "STALE_REVISION"
        headers["If-Match"] = '"2"'
        revoked = client.put(
            path, json={"role": None, "reason": "Remove test access"}, headers=headers
        )
        assert revoked.status_code == 200 and revoked.json()["revoked"] is True
        assert client.get(path).json()["revision"] == 3
        headers["If-Match"] = '"3"'
        restored = client.put(
            path, json={"role": "VIEWER", "reason": "Restore test access"}, headers=headers
        )
        assert restored.status_code == 200 and restored.json()["revoked"] is False
        assert (
            client.put(
                f"/v1/workspaces/{workspace}/members/{uuid4()}", json=body, headers=headers
            ).status_code
            == 404
        )
        with workspace_connection(test_database_url, workspace) as conn:
            viewer = issue_session(conn, user_id=other)
        client.cookies.set(SESSION_COOKIE, viewer.session_token)
        denied = client.put(
            path, json=body, headers={CSRF_HEADER: viewer.csrf_token, "If-Match": '"4"'}
        )
        assert denied.status_code == 403
        assert client.get(path).status_code == 403
    with workspace_connection(test_database_url, workspace) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE workspace_id=%s "
            "AND action='MEMBERSHIP_ADMINISTER' AND outcome='DENIED'",
            (workspace,),
        ).fetchone()
        assert row and row["n"] == 4
