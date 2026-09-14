"""Retain and export original settled model-runtime observations, never agent-authored uploads."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest
from accessforge_domain.navigator_model import validate_profile
from accessforge_domain.navigator_runtime import validate_observation


def producer(attempt_id: str) -> str:
    return "navigator-runtime:" + attempt_id


def retain(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    operation_id: str,
    observation: dict[str, Any],
) -> None:
    validate_observation(observation)
    conn.execute(
        "INSERT INTO navigator_runtime_observation(operation_id,workspace_id,observation,"
        "observation_digest) VALUES(%s,%s,%s,%s)",
        (operation_id, workspace_id, Jsonb(observation), digest(observation)),
    )


def snapshot(conn: psycopg.Connection[Any], context: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT t.*,i.status,c.model_config_digest,c.model_profile,c.manifest_digest,"
        "o.observation,o.observation_digest,p.action_id FROM navigator_model_turn t "
        "JOIN diagnosis_invocation i ON i.operation_id=t.operation_id "
        "AND i.workspace_id=t.workspace_id "
        "JOIN navigator_model_consent c ON c.id=t.consent_id AND c.workspace_id=t.workspace_id "
        "LEFT JOIN navigator_runtime_observation o ON o.operation_id=t.operation_id "
        "AND o.workspace_id=t.workspace_id "
        "LEFT JOIN navigator_planning_checkpoint p ON p.operation_id=t.operation_id "
        "AND p.workspace_id=t.workspace_id AND p.kind='ACTION_RESOLVED' "
        "WHERE t.run_id=%s AND t.attempt_id=%s ORDER BY t.action_sequence LIMIT 501",
        (context["run_id"], context["attempt_id"]),
    ).fetchall()
    if not 1 <= len(rows) <= 500:
        raise ValueError("bounded original navigator invocations unavailable")
    turns = []
    for row in rows:
        validate_profile(row["model_profile"])
        if (
            row["status"] not in {"RECORDED", "UNCONFIRMED", "NOT_CALLED"}
            or str(row["lease_id"]) != str(context["lease_id"])
            or row["lease_epoch"] != context["epoch"]
            or str(row["runner_id"]) != str(context["runner_id"])
            or row["manifest_digest"] != context["manifest_digest"]
            or digest(row["model_profile"]) != row["model_config_digest"]
        ):
            raise ValueError("navigator evidence requires its exact settled attempt and profile")
        observation = row["observation"]
        if observation is not None:
            validate_observation(observation)
            if (
                digest(observation) != row["observation_digest"]
                or observation["profile"] != row["model_profile"]
            ):
                raise ValueError("original navigator runtime observation changed")
        turns.append(
            {
                "operationId": str(row["operation_id"]),
                "actionSequence": row["action_sequence"],
                "resolvedActionId": None if row["action_id"] is None else str(row["action_id"]),
                "status": row["status"],
                "modelConfigDigest": row["model_config_digest"],
                "observation": observation,
                "observationDigest": row["observation_digest"],
            }
        )
    return {
        "format": "accessforge.model-runtime.v1",
        "runId": str(context["run_id"]),
        "attemptId": str(context["attempt_id"]),
        "manifestDigest": context["manifest_digest"],
        "producerId": producer(str(context["attempt_id"])),
        "turns": turns,
    }
