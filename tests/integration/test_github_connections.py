"""Real authorization/binding DB composition; synthetic GitHub HTTP, never live tokens."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest

from accessforge_domain.authorization import HumanPrincipal, Role
from accessforge_orchestrator.github_access import Refused as ProbeRefused
from accessforge_orchestrator.github_access import RepositoryScope
from accessforge_orchestrator.github_connections import (
    Refused,
    connect_repository,
    disconnect_repository,
)
from accessforge_persistence import migrate, unscoped_connection, workspace_connection

pytestmark = pytest.mark.integration
WS, USER, SESSION = (str(UUID(int=n)) for n in (130, 131, 132))
PRINCIPAL = HumanPrincipal(USER, WS, Role.OWNER, SESSION)
SCOPE = RepositoryScope(7, 42, 3, 13, "fixture-owner", "fixture-repository")


@pytest.fixture
def db(test_database_url: str) -> str:
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace,app_user CASCADE")
        conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'connection-test')", (WS,))
        conn.execute("INSERT INTO app_user(id,email) VALUES(%s,'connection@example.test')", (USER,))
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES(%s,%s,'OWNER')",
            (WS, USER),
        )
        conn.execute(
            "INSERT INTO user_session(id,user_id,token_hash,csrf_token_hash,expires_at) "
            "VALUES(%s,%s,'synthetic-session-hash','synthetic-csrf-hash',"
            "clock_timestamp()+interval '1 hour')",
            (SESSION, USER),
        )
    return test_database_url


def transport(db: str, calls: list[str], fault: str | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.url.host == "api.github.com"
        status = 200
        value: dict[str, Any]
        if request.method == "DELETE":
            status, value = 204, {}
            if fault in {"revoke-member", "revoke-session", "disable-user", "demote"}:
                with workspace_connection(db, WS) as conn:
                    statements = {
                        "revoke-member": (
                            "UPDATE workspace_membership SET revoked_at=clock_timestamp()"
                        ),
                        "revoke-session": "UPDATE user_session SET revoked_at=clock_timestamp()",
                        "disable-user": "UPDATE app_user SET disabled_at=clock_timestamp()",
                        "demote": "UPDATE workspace_membership SET role='VIEWER'",
                    }
                    conn.execute(statements[fault])
            if fault == "cleanup-failed":
                status = 500
        elif request.method == "POST":
            status = 201
            assert json.loads(request.content)["repository_ids"] == [13]
            value = {
                "token": "ghs_" + "x" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=59))
                .isoformat()
                .replace("+00:00", "Z"),
                "permissions": {"contents": "read", "metadata": "read"},
            }
        elif request.url.path.startswith("/repos/"):
            value = {"id": 13, "full_name": "fixture-owner/fixture-repository", "owner": {"id": 3}}
        else:
            value = {
                "id": 42,
                "app_id": 7,
                "account": {"id": 3},
                "suspended_at": None,
                "permissions": {"contents": "read", "metadata": "read"},
            }
        return httpx.Response(status, stream=httpx.ByteStream(json.dumps(value).encode()))

    return httpx.MockTransport(handle)


def test_probe_binding_audit_and_disconnect_compose(db: str) -> None:
    calls: list[str] = []
    binding = connect_repository(
        db,
        principal=PRINCIPAL,
        scope=SCOPE,
        app_jwt="a.b.c",
        allow_temporary_token_issuance=True,
        _transport=transport(db, calls),
    )
    assert calls == ["GET", "POST", "GET", "GET", "DELETE"]
    with workspace_connection(db, WS) as conn:
        row = conn.execute("SELECT * FROM github_repository_binding").fetchone()
        assert row is not None and str(row["id"]) == binding and row["account_id"] == 3
        assert "ghs_" not in repr(row)
    with pytest.raises(Refused, match="already connected"):
        connect_repository(
            db,
            principal=PRINCIPAL,
            scope=SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            _transport=transport(db, calls),
        )
    assert len(calls) == 5
    assert disconnect_repository(db, principal=PRINCIPAL, binding_id=binding)
    assert not disconnect_repository(db, principal=PRINCIPAL, binding_id=binding)
    with workspace_connection(db, WS) as conn:
        audit = conn.execute(
            "SELECT action,target_id FROM audit_event ORDER BY occurred_at"
        ).fetchall()
        assert [r["action"] for r in audit] == [
            "GITHUB_REPOSITORY_CONNECT",
            "GITHUB_REPOSITORY_DISCONNECT",
        ]
        assert all(str(r["target_id"]) == binding for r in audit)


@pytest.mark.parametrize(
    "fault", ["revoke-member", "revoke-session", "disable-user", "demote", "cleanup-failed"]
)
def test_authority_change_or_cleanup_failure_prevents_binding(db: str, fault: str) -> None:
    calls: list[str] = []
    expected_error = ProbeRefused if fault == "cleanup-failed" else Refused
    with pytest.raises(expected_error):
        connect_repository(
            db,
            principal=PRINCIPAL,
            scope=SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            _transport=transport(db, calls, fault),
        )
    assert calls[-1] == "DELETE"
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT id FROM github_repository_binding").fetchall() == []
        assert conn.execute("SELECT id FROM audit_event").fetchall() == []


@pytest.mark.parametrize("role", ["MAINTAINER", "REVIEWER", "VIEWER"])
def test_cached_owner_role_does_not_authorize_remote_probe(db: str, role: str) -> None:
    with workspace_connection(db, WS) as conn:
        conn.execute("UPDATE workspace_membership SET role=%s", (role,))
    calls: list[str] = []
    with pytest.raises(Refused):
        connect_repository(
            db,
            principal=PRINCIPAL,
            scope=SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            _transport=transport(db, calls),
        )
    assert calls == []
