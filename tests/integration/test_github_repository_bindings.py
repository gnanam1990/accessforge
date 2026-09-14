"""Real local allowlist persistence; all GitHub observations below are synthetic."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest

from accessforge_persistence import migrate, unscoped_connection, workspace_connection
from accessforge_persistence.github_bindings import (
    Refused,
    disconnect,
    record_verified,
    require_live,
)

pytestmark = pytest.mark.integration
WS, OTHER = str(UUID(int=128)), str(UUID(int=129))


@pytest.fixture
def db(test_database_url: str) -> str:
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace CASCADE")
        for workspace in (WS, OTHER):
            conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'binding-test')", (workspace,))
    return test_database_url


def record(conn: psycopg.Connection[Any], *, age: int = 0) -> str:
    return record_verified(
        conn,
        workspace_id=WS,
        app_id=7,
        installation_id=42,
        account_id=9,
        repository_id=13,
        owner="fixture-owner",
        name="fixture-repository",
        observed_at=(datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
    )


def test_reconnect_disconnect_and_explicit_rebinding(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        original = record(conn)
    with workspace_connection(db, WS) as conn:
        row = require_live(conn, workspace_id=WS, binding_id=original)
        assert (row["app_id"], row["installation_id"], row["account_id"], row["repository_id"]) == (
            7,
            42,
            9,
            13,
        )
        assert row["owner_name"] == "fixture-owner"
        with pytest.raises(psycopg.IntegrityError):
            record(conn)  # Conflict cannot silently replace a current allowlist.
        assert disconnect(conn, workspace_id=WS, binding_id=original)
        assert not disconnect(conn, workspace_id=WS, binding_id=original)
        with pytest.raises(Refused):
            require_live(conn, workspace_id=WS, binding_id=original)
        replacement = record(conn)
        assert replacement != original
    with workspace_connection(db, WS) as conn:
        with pytest.raises(Refused):
            require_live(conn, workspace_id=WS, binding_id=original)
        assert str(require_live(conn, workspace_id=WS, binding_id=replacement)["id"]) == replacement


def test_database_clock_refuses_stale_and_future_observations(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        for age in (31, -31):
            with pytest.raises(psycopg.IntegrityError):
                record(conn, age=age)
        assert conn.execute("SELECT count(*) AS n FROM github_repository_binding").fetchone() == {
            "n": 0
        }


def test_scope_isolation_and_immutable_identity(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        binding = record(conn)
        for statement in (
            "UPDATE github_repository_binding SET repository_id=14",
            "UPDATE github_repository_binding SET installation_id=43",
            "UPDATE github_repository_binding SET owner_name='changed'",
            "UPDATE github_repository_binding SET observed_at=clock_timestamp()",
            "DELETE FROM github_repository_binding",
        ):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(statement)
    with workspace_connection(db, OTHER) as conn:
        with pytest.raises(Refused):
            require_live(conn, workspace_id=WS, binding_id=binding)
        assert not disconnect(conn, workspace_id=WS, binding_id=binding)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            record(conn)
    with workspace_connection(db, WS) as conn:
        assert disconnect(conn, workspace_id=WS, binding_id=binding)
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE github_repository_binding SET revoked_at=NULL")
    with unscoped_connection(db) as conn:
        conn.execute("DELETE FROM workspace WHERE id=%s", (WS,))
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT id FROM github_repository_binding").fetchall() == []


def test_outer_rollback_leaves_no_binding(db: str) -> None:
    with pytest.raises(RuntimeError), workspace_connection(db, WS) as conn:
        record(conn)
        raise RuntimeError("synthetic rollback")
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT id FROM github_repository_binding").fetchall() == []
