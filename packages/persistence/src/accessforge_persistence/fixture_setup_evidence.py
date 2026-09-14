"""Original initial-empty-fixture evidence, not an environment or desktop attestation."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc


def producer(attempt_id: str) -> str:
    return "fixture-setup:" + attempt_id


def snapshot(conn: psycopg.Connection[Any], session: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT s.*,f.id AS fixture_id,f.nonce,f.template_digest,r.manifest_digest "
        "FROM fixture_setup_reservation s JOIN run r ON r.id=s.run_id "
        "AND r.workspace_id=s.workspace_id JOIN run_fixture_instance f ON f.run_id=s.run_id "
        "AND f.workspace_id=s.workspace_id WHERE s.run_id=%s AND s.workspace_id=%s",
        (session["run_id"], session["workspace_id"]),
    ).fetchone()
    if row is None or not isinstance(row["observation"], dict):
        raise ValueError("original confirmed fixture setup unavailable")
    context, observation = row["context"], row["observation"]
    application = observation.get("application")
    if (
        digest(context) != row["context_digest"]
        or digest(observation) != row["observation_digest"]
        or observation.get("contextDigest") != row["context_digest"]
        or observation.get("meaning") != "INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION"
        or context.get("runId") != str(session["run_id"])
        or context.get("workspaceId") != str(session["workspace_id"])
        or context.get("manifestDigest") != session["manifest_digest"]
        or row["manifest_digest"] != session["manifest_digest"]
        or context.get("fixtureId") != str(row["fixture_id"])
        or context.get("nonce") != row["nonce"]
        or context.get("templateDigest") != row["template_digest"]
        or not isinstance(application, dict)
        or application.get("nonce") != row["nonce"]
        or application.get("templateDigest") != row["template_digest"]
        or type(application.get("effectCount")) is not int
        or application["effectCount"] != 0
        or row["observed_at"] is None
    ):
        raise ValueError("retained fixture setup identity or measurement differs")
    from . import candidate_fixture_setups

    try:
        original_seed = candidate_fixture_setups.for_run(conn, run_id=str(session["run_id"]))
    except candidate_fixture_setups.Refused as exc:
        raise ValueError("original candidate seed evidence unavailable") from exc
    if observation.get("candidateSeed") != original_seed:
        raise ValueError("candidate setup artifact differs from original protected seed evidence")
    return {
        "format": "accessforge.fixture-setup.v1",
        "runId": str(session["run_id"]),
        "attemptId": str(session["attempt_id"]),
        "manifestDigest": session["manifest_digest"],
        "producerId": producer(str(session["attempt_id"])),
        "context": context,
        "contextDigest": row["context_digest"],
        "observation": observation,
        "observationDigest": row["observation_digest"],
        "confirmedAt": to_rfc3339_utc(row["observed_at"]),
    }
