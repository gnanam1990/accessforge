"""Durable PostgreSQL implementation of the planning-checkpoint sink."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg

from .checkpoints import PlanningCheckpoint


@dataclass(frozen=True, slots=True)
class PostgresPlanningCheckpointSink:
    """Commit every checkpoint in its own workspace-scoped transaction."""

    database_url: str
    workspace_id: str
    run_id: str
    attempt_id: str
    expected_run_ref: str

    async def retain(self, checkpoint: PlanningCheckpoint) -> None:
        if checkpoint.run_ref != self.expected_run_ref:
            raise ValueError(
                f"checkpoint names {checkpoint.run_ref!r}, expected {self.expected_run_ref!r}"
            )

        conn = await psycopg.AsyncConnection.connect(self.database_url)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('accessforge.workspace_id', %s, true)",
                    (self.workspace_id,),
                )
                await conn.execute(
                    """
                    INSERT INTO navigator_planning_checkpoint
                        (id, workspace_id, run_id, attempt_id, run_ref, kind, recorded_at,
                         sdk_version, provider, model_id, action, key_chord, text_value_ref,
                         dispatch_status, action_id, stop_reason)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        self.workspace_id,
                        self.run_id,
                        self.attempt_id,
                        checkpoint.run_ref,
                        checkpoint.kind.value,
                        checkpoint.recorded_at_utc,
                        checkpoint.sdk_version,
                        checkpoint.provider,
                        checkpoint.model_id,
                        checkpoint.action.value if checkpoint.action is not None else None,
                        checkpoint.key_chord,
                        checkpoint.text_value_ref,
                        checkpoint.dispatch_status,
                        checkpoint.action_id,
                        checkpoint.stop_reason,
                    ),
                )
            # Exiting the transaction commits before this method returns. The connection is not
            # shared with model work, so a later provider failure cannot roll it back.
        finally:
            await conn.close()
