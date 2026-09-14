"""Real transactional replay/RLS checks; no GitHub installation or publication proof."""

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import sql

from accessforge_orchestrator.github_webhooks import authenticate
from accessforge_persistence import migrate, unscoped_connection, workspace_connection
from accessforge_persistence.github_webhooks import Receipt, Refused, record_authenticated

pytestmark = pytest.mark.integration
WS, OTHER = str(UUID(int=126)), str(UUID(int=127))


@pytest.fixture
def db(test_database_url: str) -> str:
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace CASCADE")
        for workspace in (WS, OTHER):
            conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'webhook-test')", (workspace,))
    return test_database_url


def record(
    conn: psycopg.Connection[Any], *, delivery: int = 1, body: bytes | None = None
) -> Receipt:
    raw = body or b'{"installation":{"id":42},"repository":{"id":13}}'
    secret = b"synthetic-webhook-test-key-not-a-secret"
    source = authenticate(
        raw,
        secret=secret,
        signature="sha256="
        + hmac.new(
            secret,
            raw,
            hashlib.sha256,
        ).hexdigest(),
        delivery_id=str(UUID(int=delivery)),
    )
    return record_authenticated(
        conn,
        workspace_id=WS,
        app_id=7,
        body_digest=source.body_digest,
        installation_id=source.installation_id,
        repository_id=source.repository_id,
        delivery_id=source.delivery_id,
    )


def test_replays_survive_reconnect_and_header_aliases_cannot_be_reused(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        first = record(conn)
        assert first.new_body
    with workspace_connection(db, WS) as conn:
        assert record(conn) == Receipt(first.body_id, False)
        assert record(conn, delivery=2) == Receipt(first.body_id, False)
    with workspace_connection(db, WS) as conn:
        for delivery in (1, 2):
            with pytest.raises(Refused, match="different bytes"):
                record(conn, delivery=delivery, body=b'{"installation":{"id":42}}')
        # Caught conflicts must not leave the newly inserted body behind.
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_delivery").fetchone() == {
            "n": 2
        }


def test_concurrent_header_variants_admit_one_body(db: str) -> None:
    barrier = Barrier(2)

    def submit(delivery: int) -> Receipt:
        with workspace_connection(db, WS) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            barrier.wait(timeout=5)
            return record(conn, delivery=delivery)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, (1, 2)))
    assert len({result.body_id for result in results}) == 1
    assert sum(result.new_body for result in results) == 1


def test_workspace_isolation_and_receipt_immutability(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        receipt = record(conn)
        for table in ("github_webhook_body", "github_webhook_delivery"):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(sql.SQL("UPDATE {} SET app_id=8").format(sql.Identifier(table)))
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
    with workspace_connection(db, OTHER) as conn:
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []
        assert conn.execute("SELECT body_id FROM github_webhook_delivery").fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            record(conn)
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "INSERT INTO github_webhook_delivery(workspace_id,app_id,delivery_id,body_id) "
                "VALUES(%s,7,%s,%s)",
                (OTHER, str(UUID(int=3)), receipt.body_id),
            )
    with unscoped_connection(db) as conn:
        conn.execute("DELETE FROM workspace WHERE id=%s", (WS,))
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []


def test_outer_rollback_does_not_consume_delivery(db: str) -> None:
    with pytest.raises(RuntimeError), workspace_connection(db, WS) as conn:
        assert record(conn).new_body
        raise RuntimeError("synthetic transaction rollback")
    with workspace_connection(db, WS) as conn:
        assert record(conn).new_body
