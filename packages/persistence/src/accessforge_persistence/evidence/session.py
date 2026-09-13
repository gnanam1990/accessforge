"""Source provenance and evidence requirements for newly opened desktop sessions.

Control-plane receipts attest what the authenticated supervisor requested/reported, not that an
OS action physically happened. Its separate local journal remains a required artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authorization import (
    MachinePrincipal,
    ServiceIdentity,
    assert_may_submit_event,
)
from accessforge_domain.canonical import digest
from accessforge_persistence import sequencer

from . import artifacts


def observer_producer(credential_ref: str, attempt_id: str) -> str:
    return "observer:" + digest({"credentialRef": credential_ref, "attempt": attempt_id})


def requirements(conn: psycopg.Connection[Any], row: dict[str, Any]) -> dict[str, str]:
    environment = conn.execute(
        "SELECT e.observer_credential_ref FROM environment_manifest e JOIN sealed_manifest s "
        "ON s.environment_manifest_id=e.id WHERE s.run_id=%s",
        (row["run_id"],),
    ).fetchone()
    if environment is None:
        raise ValueError("sealed observer identity missing")
    prefix = f"supervisor:{row['id']}"
    return {
        "RUNNER_JOURNAL": prefix + ":journal",
        "ACTION_TRACE": prefix + ":actions",
        "SPEECH_TRANSCRIPT": prefix + ":reader",
        "PREFLIGHT_RECORD": prefix + ":lifecycle",
        "EFFECT_RECEIPT": observer_producer(
            environment["observer_credential_ref"], str(row["attempt_id"])
        ),
    }


def emit(
    conn: psycopg.Connection[Any],
    row: dict[str, Any],
    *,
    stream: str,
    sequence: int,
    source_id: str,
    event_type: str,
    source: dict[str, Any],
) -> None:
    principal = MachinePrincipal(
        service_identity=ServiceIdentity.SUPERVISOR,
        workspace_id=str(row["workspace_id"]),
        credential_id=str(row["id"]),
        run_id=str(row["run_id"]),
        lease_id=str(row["lease_id"]),
    )
    assert_may_submit_event(principal, event_type)
    run = conn.execute("SELECT manifest_digest FROM run WHERE id=%s", (row["run_id"],)).fetchone()
    if run is None:
        raise ValueError("sealed run missing")
    producer = f"supervisor:{row['id']}:{stream}"
    sequencer.admit_record(
        conn,
        workspace_id=str(row["workspace_id"]),
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
        lease_epoch=int(row["epoch"]),
        producer_id=producer,
        source_record_id=source_id,
        producer_sequence=sequence,
        event_type=event_type,
        manifest_digest=run["manifest_digest"],
        payload={
            "producerId": producer,
            "producerSequence": sequence,
            "sourceRecordId": source_id,
            "sourceRecordDigest": digest(source),
            "sourceRecord": source,
            "serviceIdentity": "SUPERVISOR",
            "provenance": "CONTROL_PLANE_RECEIPT",
        },
        source_time=datetime.now(UTC),
    )


def start(conn: psycopg.Connection[Any], row: dict[str, Any]) -> None:
    artifacts.declare_required_artifacts(
        conn,
        workspace_id=str(row["workspace_id"]),
        run_id=str(row["run_id"]),
        requirements=requirements(conn, row),
    )
    emit(
        conn,
        row,
        stream="lifecycle",
        sequence=1,
        source_id="session-opened",
        event_type="RUN_STARTED",
        source={"boundary": "AUTHENTICATED_SESSION_OPENED"},
    )
    preflight = conn.execute(
        "SELECT id,checks,successful FROM runner_preflight WHERE runner_id=%s "
        "ORDER BY recorded_at DESC,id DESC LIMIT 1",
        (row["runner_id"],),
    ).fetchone()
    # _live validated this exact latest record under the runner lock; never substitute an older
    # successful record for the record used by admission.
    if preflight is None or not preflight["successful"]:
        raise ValueError("admitted preflight unavailable")
    emit(
        conn,
        row,
        stream="lifecycle",
        sequence=2,
        source_id="admitted-preflight",
        event_type="PREFLIGHT_RESULT",
        source={
            "preflightId": str(preflight["id"]),
            "checks": preflight["checks"],
            "meaning": "STORED_PREFLIGHT_USED_FOR_ADMISSION",
        },
    )
