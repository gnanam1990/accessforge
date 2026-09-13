"""Committed outbox handoff with real PostgreSQL and an inert queue port, not AWS proof."""

from uuid import uuid4

import pytest

from accessforge_orchestrator.queue_delivery import publish_once
from accessforge_persistence import migrate, outbox, unscoped_connection, workspace_connection
from accessforge_persistence.transport import AwsSqsTransport, PublishedReference

pytestmark = pytest.mark.integration


def test_later_batch_rows_are_not_claimed_while_an_earlier_send_waits(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(test_database_url)
    workspace = str(uuid4())
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES (%s,'queue batch')", (workspace,))
    try:
        with workspace_connection(test_database_url, workspace) as conn:
            for _ in range(3):
                outbox.enqueue_message(
                    conn,
                    workspace_id=workspace,
                    operation_id=str(uuid4()),
                    topic="run.queued",
                    reference={"runId": str(uuid4()), "revision": 1, "status": "QUEUED"},
                )
        transport = AwsSqsTransport(
            "https://sqs.us-east-1.amazonaws.com/123456789012/accessforge",
            workspace_id=workspace,
            region_name="us-east-1",
        )
        sent: list[str] = []

        def send(message: PublishedReference) -> None:
            with workspace_connection(test_database_url, workspace) as conn:
                rows = conn.execute("SELECT * FROM outbox_message").fetchall()
                active = [row for row in rows if row["claimed_by"] and row["published_at"] is None]
                assert len(active) == 1
                assert str(active[0]["operation_id"]) == message.operation_id
                assert sum(row["claimed_by"] is None for row in rows) == 2 - len(sent)
            sent.append(message.operation_id)

        monkeypatch.setattr(transport, "publish", send)
        report = publish_once(test_database_url, transport, limit=3)
        assert report.claimed == report.published == 3
        assert report.unconfirmed == report.superseded == 0
        assert len(set(sent)) == 3
    finally:
        with unscoped_connection(test_database_url) as conn:
            conn.execute("DELETE FROM workspace WHERE id=%s", (workspace,))


@pytest.mark.parametrize("fault", [None, "send", "expired", "superseded"])
def test_publish_acknowledges_only_committed_current_claim(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    migrate(test_database_url)
    workspace, other = str(uuid4()), str(uuid4())
    with unscoped_connection(test_database_url) as conn:
        for ws in (workspace, other):
            conn.execute("INSERT INTO workspace(id,name) VALUES (%s,'queue test')", (ws,))
    try:
        for ws in (workspace, other):
            with workspace_connection(test_database_url, ws) as conn:
                outbox.enqueue_message(
                    conn,
                    workspace_id=ws,
                    operation_id=str(uuid4()),
                    topic="run.queued",
                    reference={"runId": str(uuid4()), "revision": 1, "status": "QUEUED"},
                )
        transport = AwsSqsTransport(
            "https://sqs.us-east-1.amazonaws.com/123456789012/accessforge",
            workspace_id=workspace,
            region_name="us-east-1",
        )

        def send(message: PublishedReference) -> None:
            # A separate transaction must see the committed claim before transport execution.
            with workspace_connection(test_database_url, workspace) as conn:
                row = conn.execute("SELECT * FROM outbox_message").fetchone()
                assert row is not None and row["claimed_by"].startswith("sqs-publisher-")
                assert row["attempts"] == 1 and row["published_at"] is None
                assert str(row["operation_id"]) == message.operation_id
                if fault == "expired":
                    conn.execute(
                        "UPDATE outbox_message "
                        "SET claim_expires_at=clock_timestamp()-interval '1 second'"
                    )
                if fault == "superseded":
                    conn.execute("UPDATE outbox_message SET claimed_by='new-worker', attempts=2")
            if fault == "send":
                raise RuntimeError("queue reply lost")

        monkeypatch.setattr(transport, "publish", send)
        report = publish_once(test_database_url, transport)
        assert report.claimed == 1
        assert report.published == int(fault is None)
        assert report.unconfirmed == int(fault == "send")
        assert report.superseded == int(fault in {"expired", "superseded"})
        with workspace_connection(test_database_url, workspace) as conn:
            assert outbox.unpublished_count(conn) == int(fault is not None)
        with workspace_connection(test_database_url, other) as conn:
            row = conn.execute("SELECT * FROM outbox_message").fetchone()
            assert row is not None and row["claimed_by"] is None and row["published_at"] is None
    finally:
        with unscoped_connection(test_database_url) as conn:
            conn.execute("DELETE FROM workspace WHERE id IN (%s,%s)", (workspace, other))
