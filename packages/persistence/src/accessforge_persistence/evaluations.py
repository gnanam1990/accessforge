"""Read original verifier snapshots; never reconstruct a historical verdict on reads."""

from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc


class EvaluationError(Exception):
    """Missing or corrupt evaluation identity."""


def read(conn: psycopg.Connection[Any], *, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM run_evaluation WHERE run_id=%s", (run_id,)).fetchone()
    if row is None:
        return None
    value = row["snapshot"]
    try:
        measured = digest(value)
    except (ValueError, TypeError) as exc:
        raise EvaluationError("retained evaluation snapshot is not canonical") from exc
    if (
        not isinstance(value, dict)
        or type(value.get("schemaVersion")) is not int
        or value.get("schemaVersion") != 1
        or measured != row["snapshot_digest"]
        or any(
            (
                value.get("runId") != str(row["run_id"]),
                value.get("attemptId") != str(row["attempt_id"]),
                value.get("outcome") != row["outcome"],
                value.get("manifestDigest") != row["manifest_digest"],
                value.get("evidenceSetDigest") != row["evidence_set_digest"],
                value.get("evaluatorVersion") != row["evaluator_version"],
            )
        )
    ):
        raise EvaluationError("retained evaluation snapshot integrity unavailable")
    return {
        "evaluationId": str(row["id"]),
        "snapshotDigest": row["snapshot_digest"],
        "snapshot": value,
        "recordedAt": to_rfc3339_utc(row["created_at"]),
        "meaning": "ORIGINAL_EVALUATION_SNAPSHOT",
    }
