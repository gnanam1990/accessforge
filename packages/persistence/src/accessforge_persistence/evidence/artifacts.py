"""Artifact intake: quarantine, server-side hashing, promotion, redaction and retention.

The rule this module exists to enforce is one sentence from CONTRACTS section 7: "No artifact
supplied solely by the agent becomes trusted merely because its JSON says PASS."

So an upload is not evidence. An upload is a candidate that arrives QUARANTINED, gets hashed by the
server, and is promoted only once its measured digest, its declared kind and content type, and its
manifest binding all check out. Nothing about the file's *contents* is inspected for verdicts — this
layer establishes provenance and integrity, and module 11 decides what the contents mean.

Retention deserves its own note. INV-15 says a deletion must not leave an export that still looks
fully verifiable. The way that goes wrong is deleting a row: the export then has no idea an artifact
ever existed and reports a complete evidence set. So the row outlives the bytes and says what
happened to them, and finalization treats a DELETED required artifact as missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.timestamps import to_rfc3339_utc

from .objectstore import (
    ArtifactStore,
    ArtifactStoreError,
    artifact_key,
    assert_uploadable,
    compute_digest,
)


class ArtifactError(Exception):
    """An artifact operation was refused."""


class ArtifactRejected(ArtifactError):
    """The artifact arrived and was refused. The row is kept; the bytes are not promoted."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    artifact_id: str
    kind: str
    content_digest: str
    object_key: str
    state: str
    size_bytes: int


def _now(now: str | None) -> str:
    return now or to_rfc3339_utc(datetime.now(UTC))


def declare_required_artifacts(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    requirements: dict[str, str],
    now: str | None = None,
) -> None:
    """Declare which artifact kinds this run requires, and from which producer.

    Per run rather than a fixed global list. A journey with no functional assertion needs no
    functional trace, and a requirement that is always the same list cannot be tightened for a
    journey that needs more. Declaring it here, before execution, is also what makes a *missing*
    artifact detectable: without a declaration, an absent file is indistinguishable from one that
    was never expected.
    """
    moment = _now(now)
    for kind, producer_id in requirements.items():
        conn.execute(
            """
            INSERT INTO required_artifact (id, workspace_id, run_id, kind, producer_id, declared_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, kind, producer_id) DO NOTHING
            """,
            (str(uuid.uuid4()), workspace_id, run_id, kind, producer_id, moment),
        )


def upload_to_quarantine(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    kind: str,
    producer_id: str,
    lease_epoch: int,
    manifest_digest: str,
    content_type: str,
    payload: bytes,
    now: str | None = None,
) -> StoredArtifact:
    """Accept an upload into quarantine. Never into evidence.

    The digest is computed from ``payload`` here. There is no parameter through which a caller could
    supply one, which is deliberate: a caller able to declare the hash of its own upload can declare
    the hash of bytes it did not send, and every later integrity check would then confirm the
    declaration rather than the content.

    Validation happens before the object is written, so a refused artifact leaves nothing in the
    bucket to clean up.
    """
    moment = _now(now)
    assert_uploadable(kind=kind, content_type=content_type, payload=payload)

    content_digest = compute_digest(payload)
    key = artifact_key(
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        kind=kind,
        content_digest=content_digest,
    )

    existing = conn.execute(
        """
        SELECT id, state, size_bytes, object_key FROM evidence_artifact
        WHERE attempt_id = %s AND kind = %s AND content_digest = %s
        """,
        (attempt_id, kind, content_digest),
    ).fetchone()
    if existing is not None:
        # Byte-identical re-upload. A retry after a lost response, not a second artifact, and the
        # content-addressed key means the object is already the right bytes at the right name.
        return StoredArtifact(
            artifact_id=str(existing["id"]),
            kind=kind,
            content_digest=content_digest,
            object_key=str(existing["object_key"]),
            state=str(existing["state"]),
            size_bytes=int(existing["size_bytes"]),
        )

    store.put(key=key, payload=payload, content_type=content_type)
    artifact_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO evidence_artifact
            (id, workspace_id, run_id, attempt_id, kind, producer_id, lease_epoch, content_digest,
             content_type, size_bytes, object_key, manifest_digest, state, uploaded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'QUARANTINED', %s)
        """,
        (
            artifact_id,
            workspace_id,
            run_id,
            attempt_id,
            kind,
            producer_id,
            lease_epoch,
            content_digest,
            content_type,
            len(payload),
            key,
            manifest_digest,
            moment,
        ),
    )
    return StoredArtifact(
        artifact_id=artifact_id,
        kind=kind,
        content_digest=content_digest,
        object_key=key,
        state="QUARANTINED",
        size_bytes=len(payload),
    )


def promote(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    artifact_id: str,
    expected_manifest_digest: str,
    now: str | None = None,
) -> None:
    """Promote a quarantined artifact to evidence, re-reading the bytes to confirm the digest.

    The re-read is the point. The digest was computed at upload from what arrived in memory; this
    reads back what the store actually holds and hashes it again. Without that, an object swapped in
    the bucket between upload and promotion keeps its row's digest and becomes trusted evidence with
    a hash describing bytes that are no longer there.

    The manifest binding is checked against a value the *caller* supplies from the sealed manifest,
    not against the artifact's own column. Comparing the row to itself would always agree.
    """
    moment = _now(now)
    row = conn.execute(
        """
        SELECT object_key, content_digest, manifest_digest, state
        FROM evidence_artifact WHERE id = %s FOR UPDATE
        """,
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ArtifactError("no such artifact in this workspace")
    if str(row["state"]) == "PROMOTED":
        return  # idempotent
    if str(row["state"]) == "REJECTED":
        raise ArtifactError("a rejected artifact is not promotable; upload a new one")

    if str(row["manifest_digest"]) != expected_manifest_digest:
        _reject(
            conn,
            artifact_id=artifact_id,
            reason=(
                "the artifact is bound to a different sealed manifest than the run being "
                "finalized, so it is evidence about a different set of bytes (INV-03)"
            ),
        )
        raise ArtifactRejected("artifact manifest binding does not match the run")

    stored = store.get(key=str(row["object_key"]))
    actual = compute_digest(stored)
    if actual != str(row["content_digest"]):
        _reject(
            conn,
            artifact_id=artifact_id,
            reason=(
                f"the stored object hashes to {actual} and the row records "
                f"{row['content_digest']}; the bytes changed after upload"
            ),
        )
        raise ArtifactRejected("stored artifact does not match its recorded digest")

    conn.execute(
        "UPDATE evidence_artifact SET state = 'PROMOTED', promoted_at = %s WHERE id = %s",
        (moment, artifact_id),
    )


def _reject(conn: psycopg.Connection[dict[str, Any]], *, artifact_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE evidence_artifact SET state = 'REJECTED', rejected_reason = %s WHERE id = %s",
        (reason, artifact_id),
    )


def attach_redacted_view(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    artifact_id: str,
    redacted_payload: bytes,
    content_type: str,
    reason: str,
    now: str | None = None,
) -> str:
    """Store a redacted view as a separate object with its own digest.

    Not a flag on the original row. One digest describing two different byte streams would make any
    later verification check the wrong one, and an export that cited "the transcript" would be
    ambiguous about which transcript it meant.

    The original is retained and its state becomes REDACTED, which finalization and export both
    read. A redacted artifact is present but not the whole story, and that has to be visible
    rather than inferred from a shorter file.
    """
    moment = _now(now)
    row = conn.execute(
        "SELECT workspace_id, run_id, attempt_id, kind, content_digest FROM evidence_artifact "
        "WHERE id = %s FOR UPDATE",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ArtifactError("no such artifact in this workspace")

    redacted_digest = compute_digest(redacted_payload)
    if redacted_digest == str(row["content_digest"]):
        raise ArtifactError(
            "the redacted view is byte-identical to the original, so nothing was redacted. "
            "Recording it would claim a redaction that did not happen."
        )

    key = (
        artifact_key(
            workspace_id=str(row["workspace_id"]),
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            kind=str(row["kind"]),
            content_digest=redacted_digest,
        )
        + ".redacted"
    )
    store.put(key=key, payload=redacted_payload, content_type=content_type)
    conn.execute(
        """
        UPDATE evidence_artifact
           SET redacted_object_key = %s, redacted_digest = %s, retention = 'REDACTED',
               retention_changed_at = %s, retention_reason = %s
         WHERE id = %s
        """,
        (key, redacted_digest, moment, reason, artifact_id),
    )
    return redacted_digest


def delete_artifact_bytes(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    artifact_id: str,
    reason: str,
    now: str | None = None,
) -> None:
    """Delete the bytes and keep the record saying so.

    INV-15 in one function. Deleting the row instead would leave an export with no idea the artifact
    ever existed, reporting a complete evidence set for a run whose evidence is gone. The row
    survives, its retention becomes DELETED, and finalization counts a DELETED required artifact as
    missing.
    """
    moment = _now(now)
    row = conn.execute(
        "SELECT object_key, redacted_object_key FROM evidence_artifact WHERE id = %s FOR UPDATE",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ArtifactError("no such artifact in this workspace")

    store.delete(key=str(row["object_key"]))
    if row["redacted_object_key"] is not None:
        store.delete(key=str(row["redacted_object_key"]))

    conn.execute(
        """
        UPDATE evidence_artifact
           SET retention = 'DELETED', retention_changed_at = %s, retention_reason = %s
         WHERE id = %s
        """,
        (moment, reason, artifact_id),
    )


def missing_required_artifacts(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, attempt_id: str
) -> list[dict[str, str]]:
    """Required artifact kinds that are not present, promoted and retained.

    Three ways to be missing, and the reason distinguishes them because they lead an operator
    somewhere different: never uploaded, uploaded but not promoted, or promoted and later deleted.
    """
    required = conn.execute(
        "SELECT kind, producer_id FROM required_artifact WHERE run_id = %s", (run_id,)
    ).fetchall()
    present = {
        (str(r["kind"]), str(r["producer_id"])): (str(r["state"]), str(r["retention"]))
        for r in conn.execute(
            "SELECT kind, producer_id, state, retention FROM evidence_artifact "
            "WHERE attempt_id = %s",
            (attempt_id,),
        ).fetchall()
    }

    missing: list[dict[str, str]] = []
    for row in required:
        key = (str(row["kind"]), str(row["producer_id"]))
        found = present.get(key)
        if found is None:
            missing.append({"kind": key[0], "producer": key[1], "reason": "NEVER_UPLOADED"})
            continue
        state, retention = found
        if state != "PROMOTED":
            missing.append({"kind": key[0], "producer": key[1], "reason": f"STATE_{state}"})
        elif retention == "DELETED":
            missing.append({"kind": key[0], "producer": key[1], "reason": "BYTES_DELETED"})
    return missing


def verify_stored_integrity(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    attempt_id: str,
) -> list[dict[str, str]]:
    """Re-hash every promoted artifact against what the store now holds.

    Used by recovery and by export. Reports discrepancies rather than raising on the first one,
    because an operator restoring from backup needs the whole list, not the alphabetically first
    problem.
    """
    problems: list[dict[str, str]] = []
    rows = conn.execute(
        "SELECT id, object_key, content_digest, retention FROM evidence_artifact "
        "WHERE attempt_id = %s AND state = 'PROMOTED'",
        (attempt_id,),
    ).fetchall()
    for row in rows:
        if str(row["retention"]) == "DELETED":
            continue  # its absence is recorded, not a discrepancy
        try:
            actual = compute_digest(store.get(key=str(row["object_key"])))
        except ArtifactStoreError as exc:
            problems.append({"artifact": str(row["id"]), "problem": f"UNREADABLE: {exc}"})
            continue
        if actual != str(row["content_digest"]):
            problems.append(
                {
                    "artifact": str(row["id"]),
                    "problem": (
                        f"DIGEST_MISMATCH: recorded {row['content_digest']}, stored {actual}"
                    ),
                }
            )
    return problems
