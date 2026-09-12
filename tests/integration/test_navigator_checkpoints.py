"""Real PostgreSQL proof for privacy-safe navigator planning checkpoints."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from accessforge_domain.canonical import digest
from accessforge_navigation_tools import ActionName
from accessforge_orchestrator.navigator import (
    CheckpointKind,
    PlanningCheckpoint,
    PostgresPlanningCheckpointSink,
)
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0x12A))
WS_B = str(uuid.UUID(int=0x12B))
NOW = "2026-09-12T12:00:00Z"


@pytest.fixture()
def seeded(test_database_url: str) -> Iterator[tuple[str, str, str, str]]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Alpha')", (WS_A,))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Beta')", (WS_B,))

    with workspace_connection(test_database_url, WS_A) as conn:
        run_id = runs.create_run(
            conn,
            workspace_id=WS_A,
            manifest_digest=digest({"manifest": "navigator-checkpoint"}),
        )
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS_A, lease_epoch=1)
    yield test_database_url, run_id, attempt_id, f"{run_id}:{attempt_id}:epoch-1"


@pytest.mark.asyncio
async def test_checkpoints_commit_in_order_and_are_invisible_to_another_workspace(
    seeded: tuple[str, str, str, str],
) -> None:
    database_url, run_id, attempt_id, run_ref = seeded
    sink = PostgresPlanningCheckpointSink(
        database_url=database_url,
        workspace_id=WS_A,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_run_ref=run_ref,
    )
    await sink.retain(
        PlanningCheckpoint(
            run_ref=run_ref,
            kind=CheckpointKind.MODEL_CALL_STARTED,
            recorded_at_utc=NOW,
            sdk_version="1.55.1",
            provider="amazon-bedrock",
            model_id="global.anthropic.claude-sonnet-4-6",
        )
    )
    await sink.retain(
        PlanningCheckpoint(
            run_ref=run_ref,
            kind=CheckpointKind.ACTION_PROPOSED,
            recorded_at_utc=NOW,
            action=ActionName.TYPE_TEXT,
            text_value_ref="fullName",
        )
    )

    with workspace_connection(database_url, WS_A) as conn:
        rows = conn.execute(
            "SELECT kind, action, text_value_ref FROM navigator_planning_checkpoint "
            "ORDER BY checkpoint_sequence"
        ).fetchall()
    assert rows == [
        {"kind": "MODEL_CALL_STARTED", "action": None, "text_value_ref": None},
        {"kind": "ACTION_PROPOSED", "action": "TYPE_TEXT", "text_value_ref": "fullName"},
    ]

    with workspace_connection(database_url, WS_B) as conn:
        assert conn.execute("SELECT * FROM navigator_planning_checkpoint").fetchall() == []


def test_checkpoint_schema_has_no_privileged_content_columns(
    seeded: tuple[str, str, str, str],
) -> None:
    database_url, _, _, _ = seeded
    with workspace_connection(database_url, WS_A) as conn:
        columns = {
            str(row["column_name"])
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'navigator_planning_checkpoint'"
            ).fetchall()
        }
    assert columns.isdisjoint(
        {
            "announcement",
            "raw_text",
            "dom",
            "source",
            "screenshot",
            "observer_receipt",
            "assertion_expectation",
        }
    )
