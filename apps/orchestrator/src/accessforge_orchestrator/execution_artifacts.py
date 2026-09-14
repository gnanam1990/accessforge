"""Trusted post-STOP artifact retention, not evaluation or a public upload service.

The operator supplies the supervisor's private spool bytes. These are cross-checked, not remotely
attested: production spool ownership and service isolation remain deployment responsibilities.
Four other artifacts are derived from existing authenticated records, never caller JSON verdicts.
All keys/rows are reserved before object I/O so a crash leaves tracked quarantine, not lost work.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from accessforge_domain.canonical import canonicalize, digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc
from accessforge_persistence import navigator_runtime, sequencer, workspace_connection
from accessforge_persistence.evidence import artifacts
from accessforge_persistence.evidence.objectstore import (
    MAX_ARTIFACT_BYTES,
    ArtifactStore,
    artifact_key,
    assert_uploadable,
    compute_digest,
)
from accessforge_persistence.evidence.session import requirements


class Refused(Exception):
    """The stopped attempt or its immutable artifact bytes could not be established."""


class ExecutionArtifactStore(ArtifactStore, Protocol):
    def get_bounded(self, *, key: str, max_bytes: int) -> bytes: ...

    def put_create_only(self, *, key: str, payload: bytes, content_type: str) -> str: ...


@dataclass
class _BoundedStore:
    delegate: ExecutionArtifactStore

    def get(self, *, key: str) -> bytes:
        return self.delegate.get_bounded(key=key, max_bytes=MAX_ARTIFACT_BYTES)

    def put(self, *, key: str, payload: bytes, content_type: str) -> str:
        return self.delegate.put_create_only(key=key, payload=payload, content_type=content_type)

    def exists(self, *, key: str) -> bool:
        return self.delegate.exists(key=key)

    def delete(self, *, key: str) -> None:
        self.delegate.delete(key=key)


def _context(conn: psycopg.Connection[Any], run_id: str) -> dict[str, Any]:
    ticket = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE run_id=%s", (run_id,)
    ).fetchone()
    if ticket is None:
        raise Refused("stopped session unavailable")
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (ticket["runner_id"],))
    run = conn.execute("SELECT * FROM run WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
    lease = conn.execute(
        "SELECT * FROM desktop_lease WHERE id=%s FOR UPDATE", (ticket["lease_id"],)
    ).fetchone()
    if (
        run is None
        or lease is None
        or run["status"] != "FINALIZING"
        or run["unresolved_action"]
        or run["quarantined"]
        or run["lease_epoch"] != ticket["epoch"]
        or lease["run_id"] != ticket["run_id"]
        or lease["epoch"] != ticket["epoch"]
        or lease["release_reason"] != "STOP_ACKNOWLEDGED"
        or lease["released_at"] is None
        or lease["stop_acknowledged_at"] is None
        or lease["stop_acknowledged_at"] != run["stop_acknowledged_at"]
        or lease["stop_acknowledged_epoch"] != ticket["epoch"]
        or run["stop_acknowledged_epoch"] != ticket["epoch"]
    ):
        raise Refused("exact normal STOP handoff unavailable")
    ticket = conn.execute(
        "SELECT t.* FROM supervisor_dispatch_ticket t JOIN supervisor_execution_session s "
        "ON s.ticket_id=t.id WHERE t.id=%s AND t.accepted_at IS NOT NULL "
        "AND t.revoked_at=%s AND s.revoked_at=t.revoked_at FOR SHARE OF t,s",
        (ticket["id"], lease["stop_acknowledged_at"]),
    ).fetchone()
    if ticket is None or run["cancel_requested_at"] is not None:
        raise Refused("session did not close through the normal STOP handoff")
    row = dict(ticket)
    row["manifest_digest"] = run["manifest_digest"]
    return row


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise Refused("duplicate journal field")
        value[key] = item
    return value


def _journal(payload: bytes, actions: list[dict[str, Any]], row: dict[str, Any]) -> None:
    if not payload or len(payload) > MAX_ARTIFACT_BYTES or not payload.endswith(b"\n"):
        raise Refused("bounded complete journal bytes required")
    try:
        entries = [
            json.loads(line, object_pairs_hook=_unique_object) for line in payload.splitlines()
        ]
    except (ValueError, UnicodeError) as exc:
        raise Refused("malformed journal; no discarded lines") from exc
    if not actions or len(entries) != 2 * len(actions):
        raise Refused("journal does not cover all actions")
    last_monotonic = -1.0
    for index, action in enumerate(actions, start=1):
        intent, result = entries[(index - 1) * 2 : index * 2]
        if not isinstance(intent, dict) or not isinstance(result, dict):
            raise Refused("journal record is not an object")
        expected = {
            "actionId": f"{row['lease_id']}:{row['epoch']}:{index}",
            "serverActionId": str(action["id"]),
            "leaseId": str(row["lease_id"]),
            "epoch": int(row["epoch"]),
            "sequence": index,
            "action": action["action"],
        }
        if action["key_chord"] is not None:
            expected["keyChord"] = action["key_chord"]
        if action["text_value"] is not None:
            expected["text"] = action["text_value"]
        stamp = intent.get("intentAtUtc")
        if not isinstance(stamp, str):
            raise Refused("journal timestamp missing")
        try:
            parse_rfc3339_utc(stamp, field="intentAtUtc")
        except ValueError as exc:
            raise Refused("journal timestamp malformed") from exc
        expected["intentAtUtc"] = stamp
        monotonic = result.get("dispatchedAtMonotonic")
        if isinstance(monotonic, bool) or not isinstance(monotonic, (int, float)):
            raise Refused("journal monotonic timestamp malformed")
        if (
            intent != expected
            or type(intent.get("epoch")) is not int
            or type(intent.get("sequence")) is not int
            or type(result.get("epoch")) is not int
            or type(result.get("sequence")) is not int
            or type(monotonic) not in (int, float)
            or not math.isfinite(monotonic)
            or monotonic < last_monotonic
            or action["action_sequence"] != index
            or action["lease_id"] != row["lease_id"]
            or action["epoch"] != row["epoch"]
            or action["dispatched_at"] is None
            or action["result_at"] is None
            or action["result_status"] not in {"SUCCEEDED", "FAILED"}
            or result
            != {**expected, "result": action["result_status"], "dispatchedAtMonotonic": monotonic}
        ):
            raise Refused("journal identity/result differs from the retained action")
        last_monotonic = monotonic
    if actions[-1]["action"] != "STOP" or actions[-1]["result_status"] != "SUCCEEDED":
        raise Refused("journal has no successful STOP tail")


def _bundle(
    conn: psycopg.Connection[Any], row: dict[str, Any], journal: bytes
) -> dict[str, tuple[str, str, bytes]]:
    required = requirements(conn, row)
    declared = conn.execute(
        "SELECT kind,producer_id FROM required_artifact WHERE run_id=%s", (row["run_id"],)
    ).fetchall()
    if not set(required.items()) <= {(r["kind"], r["producer_id"]) for r in declared}:
        raise Refused("original artifact declarations unavailable")
    actions = conn.execute(
        "SELECT * FROM runner_action WHERE run_id=%s AND attempt_id=%s ORDER BY action_sequence",
        (row["run_id"], row["attempt_id"]),
    ).fetchall()
    _journal(journal, actions, row)
    producers = {
        kind: producer
        for kind, producer in required.items()
        if kind not in {"RUNNER_JOURNAL", "MODEL_RUNTIME", "FIXTURE_SETUP", "FUNCTIONAL_REGRESSION"}
    }
    streams = conn.execute(
        "SELECT * FROM producer_stream WHERE attempt_id=%s", (row["attempt_id"],)
    ).fetchall()
    tails = {s["producer_id"]: s["closed_at_sequence"] for s in streams}
    if set(tails) != set(producers.values()) or any(
        s["closed_at_sequence"] is None or s["closed_at_sequence"] != s["admitted_through"]
        for s in streams
    ):
        raise Refused("closed evidence streams unavailable")
    records = conn.execute(
        "SELECT * FROM canonical_event WHERE run_id=%s AND attempt_id=%s ORDER BY sequence",
        (row["run_id"], row["attempt_id"]),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {p: [] for p in producers.values()}
    previous = sequencer.GENESIS_HASH
    for index, event in enumerate(records, start=1):
        payload = event["payload"]
        if (
            not isinstance(payload, dict)
            or event["sequence"] != index
            or event["previous_event_hash"] != previous
            or event["payload_digest"] != digest(payload)
            or event["manifest_digest"] != row["manifest_digest"]
            or event["lease_epoch"] != row["epoch"]
            or payload.get("producerId") not in grouped
            or payload.get("sourceRecordDigest") != digest(payload.get("sourceRecord"))
        ):
            raise Refused("canonical artifact source integrity unavailable")
        producer = payload["producerId"]
        if payload.get("producerSequence") != len(grouped[producer]) + 1:
            raise Refused("artifact source sequence gap")
        record = {
            "sequence": index,
            "eventType": event["event_type"],
            "sourceTime": to_rfc3339_utc(event["source_time"]),
            "payloadDigest": event["payload_digest"],
            "previousEventHash": previous,
        }
        previous = digest(record)
        grouped[producer].append({**record, "eventId": str(event["event_id"]), "payload": payload})
    if any(len(grouped[p]) != tails[p] for p in grouped):
        raise Refused("artifact sources do not cover closed tails")
    result = {"RUNNER_JOURNAL": (required["RUNNER_JOURNAL"], "application/x-ndjson", journal)}
    if "MODEL_RUNTIME" in required:
        try:
            model_snapshot = navigator_runtime.snapshot(conn, row)
        except ValueError as exc:
            raise Refused("original settled navigator runtime evidence unavailable") from exc
        result["MODEL_RUNTIME"] = (
            required["MODEL_RUNTIME"],
            "application/json",
            canonicalize(model_snapshot).encode(),
        )
    if "FIXTURE_SETUP" in required:
        from accessforge_persistence.fixture_setup_evidence import snapshot as setup_snapshot

        try:
            original_setup = setup_snapshot(conn, row)
        except ValueError as exc:
            raise Refused("original confirmed fixture setup evidence unavailable") from exc
        result["FIXTURE_SETUP"] = (
            required["FIXTURE_SETUP"],
            "application/json",
            canonicalize(original_setup).encode(),
        )
    if "FUNCTIONAL_REGRESSION" in required:
        from accessforge_persistence.functional_regression_evidence import (
            Refused as FunctionalRefused,
        )
        from accessforge_persistence.functional_regression_evidence import (
            snapshot as functional_snapshot,
        )

        try:
            original_functional = functional_snapshot(conn, row)
        except FunctionalRefused as exc:
            raise Refused("original completed functional regression evidence unavailable") from exc
        result["FUNCTIONAL_REGRESSION"] = (
            required["FUNCTIONAL_REGRESSION"],
            "application/json",
            canonicalize(original_functional).encode(),
        )
    for kind, producer in producers.items():
        content = {
            "format": "accessforge.execution-evidence.v1",
            "runId": str(row["run_id"]),
            "attemptId": str(row["attempt_id"]),
            "manifestDigest": row["manifest_digest"],
            "producerId": producer,
            "closedAtSequence": tails[producer],
            "records": grouped[producer],
        }
        result[kind] = (
            producer,
            "application/json",
            canonicalize(content).encode(),
        )
    return result


def retain_bundle(
    database_url: str,
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    journal: bytes,
) -> list[str]:
    """Reserve an immutable bundle, then resume tracked quarantine writes; never overwrite evidence.

    Each object is committed separately. A partial failure is observable and retryable with the
    same exact spool bytes. Deleted/rejected/conflicting artifacts cannot be resurrected by retry.
    """
    bounded = _BoundedStore(store)
    planned: list[tuple[str, str, str, bytes]] = []
    with workspace_connection(database_url, workspace_id) as conn:
        row = _context(conn, run_id)
        bundle = _bundle(conn, row, journal)
        for kind, (producer, content_type, payload) in bundle.items():
            assert_uploadable(kind=kind, content_type=content_type, payload=payload)
            measured = compute_digest(payload)
            existing = conn.execute(
                "SELECT * FROM evidence_artifact WHERE attempt_id=%s AND kind=%s FOR UPDATE",
                (row["attempt_id"], kind),
            ).fetchall()
            key = artifact_key(
                workspace_id=workspace_id,
                run_id=run_id,
                attempt_id=str(row["attempt_id"]),
                kind=kind,
                content_digest=measured,
            )
            if existing:
                old = existing[0]
                if len(existing) != 1 or any(
                    (
                        old["content_digest"] != measured,
                        old["producer_id"] != producer,
                        old["manifest_digest"] != row["manifest_digest"],
                        old["object_key"] != key,
                        old["lease_epoch"] != row["epoch"],
                        old["content_type"] != content_type,
                        old["size_bytes"] != len(payload),
                        old["retention"] != "RETAINED",
                        old["state"] not in {"QUARANTINED", "PROMOTED"},
                    )
                ):
                    raise Refused("artifact conflicts with retained or deleted evidence")
                artifact_id = str(old["id"])
            else:
                artifact_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO evidence_artifact(id,workspace_id,run_id,attempt_id,kind,"
                    "producer_id,lease_epoch,content_digest,content_type,size_bytes,object_key,"
                    "manifest_digest,state) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'QUARANTINED')",
                    (
                        artifact_id,
                        workspace_id,
                        run_id,
                        row["attempt_id"],
                        kind,
                        producer,
                        row["epoch"],
                        measured,
                        content_type,
                        len(payload),
                        key,
                        row["manifest_digest"],
                    ),
                )
            planned.append((artifact_id, key, content_type, payload))
    for artifact_id, key, content_type, payload in planned:
        with workspace_connection(database_url, workspace_id) as conn:
            current = _context(conn, run_id)
            if current != row:
                raise Refused("stopped attempt changed before artifact write")
            artifact = conn.execute(
                "SELECT * FROM evidence_artifact WHERE id=%s FOR UPDATE",
                (artifact_id,),
            ).fetchone()
            if (
                artifact is None
                or artifact["retention"] != "RETAINED"
                or artifact["state"] == "REJECTED"
                or artifact["object_key"] != key
                or artifact["content_digest"] != compute_digest(payload)
                or artifact["content_type"] != content_type
                or artifact["size_bytes"] != len(payload)
                or artifact["attempt_id"] != row["attempt_id"]
                or artifact["manifest_digest"] != row["manifest_digest"]
                or artifact["lease_epoch"] != row["epoch"]
            ):
                raise Refused("artifact is no longer retainable")
            if bounded.exists(key=key):
                if compute_digest(bounded.get(key=key)) != compute_digest(payload):
                    raise Refused("stored bytes differ; no overwrite or inferred success")
            elif artifact["state"] == "PROMOTED":
                raise Refused("promoted bytes missing; no resurrection")
            else:
                bounded.put(key=key, payload=payload, content_type=content_type)
            artifacts.promote(
                conn,
                bounded,
                artifact_id=artifact_id,
                expected_manifest_digest=row["manifest_digest"],
            )
    return [item[0] for item in planned]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--journal", required=True, help="private regular-file supervisor spool")
    args = parser.parse_args()
    try:
        # Do not read symlinks, devices, pipes or shared-writable journal paths.
        fd = os.open(args.journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as spool:
            info = os.fstat(spool.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise Refused("private owned regular journal required")
            journal = spool.read(MAX_ARTIFACT_BYTES + 1)
        from accessforge_orchestrator.maintenance.purge_worker import _store_from_environment

        ids = retain_bundle(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            _store_from_environment(),
            workspace_id=str(args.workspace_id),
            run_id=str(args.run_id),
            journal=journal,
        )
    except Exception:  # noqa: BLE001 - never log spool bytes or credential-bearing SDK errors
        print(
            "ARTIFACT_RETENTION_INCOMPLETE; tracked quarantine may be resumed with identical bytes"
        )
        raise SystemExit(1) from None
    print(f"ARTIFACTS_RETAINED count={len(ids)}; outcome=NOT_EVALUATED")


if __name__ == "__main__":
    main()
