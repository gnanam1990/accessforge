"""Artifact intake against a real S3-compatible store.

Against MinIO rather than a fake. A fake would pass every assertion about size limits and content
types while proving nothing about what only a real store does: server-side storage of the bytes we
actually sent, the specific error a missing key produces, and the fact that an object can be swapped
underneath a row that still records the old digest. That last one is the reason `promote` re-reads,
and it cannot be tested at all without a store to swap the object in.

Requirements: FR-006, FR-014, FR-015, FR-020. Invariants: INV-03, INV-06, INV-07, INV-11, INV-15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    evidence,
    migrate,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xA0))
WS_OTHER = str(uuid.UUID(int=0xA1))
MANIFEST = digest({"m": "10"})
OTHER_MANIFEST = digest({"m": "10-other"})
SUPERVISOR = "supervisor:mac-01"
TRANSCRIPT = b'{"phrases": ["Email, invalid entry"]}'


@pytest.fixture(scope="session")
def store() -> evidence.S3ArtifactStore:
    """The real object store, or a failed test saying it is absent.

    Deliberately not a skip. A skipped artifact suite on a machine with no object store looks like a
    passing build, and module 10's whole point is that an unavailable store blocks finalization
    rather than being quietly worked around.
    """
    endpoint = os.environ.get("OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.fail(
            "OBJECT_STORE_ENDPOINT is not configured. The artifact suite runs against a real "
            "S3-compatible store; there is no fallback, because a filesystem stand-in would pass "
            "every one of these assertions while proving none of them."
        )
    settings = evidence.S3Settings(
        endpoint_url=endpoint,
        access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
        secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
        bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
    )
    s3 = evidence.S3ArtifactStore(settings)
    s3.ensure_bucket()
    return s3


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _run_and_attempt(url: str, workspace: str = WS) -> tuple[str, str]:
    with workspace_connection(url, workspace) as conn:
        run_id = runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=workspace, lease_epoch=1)
    return run_id, attempt_id


def _upload(
    url: str,
    s3: evidence.S3ArtifactStore,
    run_id: str,
    attempt_id: str,
    *,
    payload: bytes = TRANSCRIPT,
    kind: str = "SPEECH_TRANSCRIPT",
    content_type: str = "application/json",
    manifest: str = MANIFEST,
    workspace: str = WS,
) -> evidence.StoredArtifact:
    with workspace_connection(url, workspace) as conn:
        return evidence.upload_to_quarantine(
            conn,
            s3,
            workspace_id=workspace,
            run_id=run_id,
            attempt_id=attempt_id,
            kind=kind,
            producer_id=SUPERVISOR,
            lease_epoch=1,
            manifest_digest=manifest,
            content_type=content_type,
            payload=payload,
        )


# --- the object store is real --------------------------------------------------------------------


def test_an_uploaded_artifact_is_actually_in_the_store(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The bytes are read back from the store itself, not from the row that describes them."""
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    assert store.get(key=artifact.object_key) == TRANSCRIPT


def test_the_digest_is_the_servers_own_hash_of_what_arrived(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """There is no parameter through which a caller could declare one.

    An uploader able to name the hash of its own upload can name the hash of bytes it did not send,
    and every later integrity check would confirm the declaration rather than the content.
    """
    import inspect

    params = set(inspect.signature(evidence.upload_to_quarantine).parameters)
    assert not any("digest" in p for p in params if p != "manifest_digest"), sorted(params)

    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    assert artifact.content_digest == evidence.compute_digest(TRANSCRIPT)


def test_an_upload_arrives_quarantined_rather_than_as_evidence(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    assert _upload(db, store, run_id, attempt_id).state == "QUARANTINED"


def test_promotion_re_reads_the_bytes_and_catches_a_swapped_object(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The reason `promote` re-reads, and a case a fake store could not produce.

    The digest was computed at upload from what arrived in memory. Without the re-read, an object
    replaced in the bucket between upload and promotion keeps its row's digest and becomes trusted
    evidence carrying a hash of bytes that are no longer there.
    """
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)

    store.put(
        key=artifact.object_key,
        payload=b'{"phrases": ["Email"]}',
        content_type="application/json",
    )

    with workspace_connection(db, WS) as conn:
        with pytest.raises(evidence.ArtifactRejected, match="does not match its recorded digest"):
            evidence.promote(
                conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
            )
        row = conn.execute(
            "SELECT state, rejected_reason FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()
    assert row is not None and str(row["state"]) == "REJECTED"
    assert "bytes changed after upload" in str(row["rejected_reason"])


def test_an_untouched_artifact_promotes(db: str, store: evidence.S3ArtifactStore) -> None:
    """Allowed-path control. Without it the checks above could pass on a promote that never runs."""
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        evidence.promote(
            conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
        )
        row = conn.execute(
            "SELECT state, promoted_at FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()
    assert row is not None and str(row["state"]) == "PROMOTED"
    assert row["promoted_at"] is not None


def test_promotion_is_idempotent(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        for _ in range(2):
            evidence.promote(
                conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
            )


def test_an_artifact_bound_to_another_manifest_is_rejected(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """INV-03. Evidence about a different set of bytes is not evidence about this run."""
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id, manifest=OTHER_MANIFEST)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(evidence.ArtifactRejected, match="manifest binding"):
            evidence.promote(
                conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
            )


def test_a_byte_identical_reupload_collapses(db: str, store: evidence.S3ArtifactStore) -> None:
    """A retry after a lost response is one artifact, not two."""
    run_id, attempt_id = _run_and_attempt(db)
    first = _upload(db, store, run_id, attempt_id)
    second = _upload(db, store, run_id, attempt_id)
    assert first.artifact_id == second.artifact_id


def test_different_bytes_for_the_same_kind_are_two_artifacts(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    first = _upload(db, store, run_id, attempt_id)
    second = _upload(db, store, run_id, attempt_id, payload=b'{"phrases": ["Something else"]}')
    assert first.artifact_id != second.artifact_id
    assert first.object_key != second.object_key


# --- what is refused before it is stored ---------------------------------------------------------


def test_an_oversized_artifact_is_refused(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    with pytest.raises(evidence.ArtifactStoreError, match="exceeds"):
        _upload(db, store, run_id, attempt_id, payload=b"x" * (evidence.MAX_ARTIFACT_BYTES + 1))


def test_an_empty_artifact_is_refused(db: str, store: evidence.S3ArtifactStore) -> None:
    """A producer with nothing to say closes its stream with a watermark."""
    run_id, attempt_id = _run_and_attempt(db)
    with pytest.raises(evidence.ArtifactStoreError, match="not evidence"):
        _upload(db, store, run_id, attempt_id, payload=b"")


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml", "application/javascript"])
def test_a_renderable_content_type_is_refused(
    db: str, store: evidence.S3ArtifactStore, content_type: str
) -> None:
    """The reason the allowlist exists.

    A stored HTML or SVG "transcript" executes when someone opens it from an export, which is a
    stored cross-site scripting payload with a chain of custody.
    """
    run_id, attempt_id = _run_and_attempt(db)
    with pytest.raises(evidence.ArtifactStoreError, match="not permitted"):
        _upload(db, store, run_id, attempt_id, content_type=content_type)


def test_a_refused_upload_leaves_nothing_in_the_bucket(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Validation runs before the object is written, so there is nothing to clean up."""
    run_id, attempt_id = _run_and_attempt(db)
    payload = b"<html>not a transcript</html>"
    key = evidence.artifact_key(
        workspace_id=WS,
        run_id=run_id,
        attempt_id=attempt_id,
        kind="SPEECH_TRANSCRIPT",
        content_digest=evidence.compute_digest(payload),
    )
    with pytest.raises(evidence.ArtifactStoreError):
        _upload(db, store, run_id, attempt_id, payload=payload, content_type="text/html")
    assert store.exists(key=key) is False


# --- keys are constructed, never accepted --------------------------------------------------------


def test_an_object_key_is_scoped_to_its_workspace(db: str) -> None:
    """INV-07 outside PostgreSQL. Row-level security protects rows and nothing in a bucket."""
    key = evidence.artifact_key(
        workspace_id=WS,
        run_id=str(uuid.UUID(int=1)),
        attempt_id=str(uuid.UUID(int=2)),
        kind="SPEECH_TRANSCRIPT",
        content_digest="a" * 64,
    )
    assert key.startswith(f"workspaces/{WS}/")


@pytest.mark.parametrize(
    "bad",
    ["../../other-tenant", "/absolute", "with space", "line\nbreak", "", "not-a-uuid"],
)
def test_a_key_component_that_is_not_an_identifier_is_refused(bad: str) -> None:
    """Validating a caller-supplied key would mean enumerating every way a path can escape a prefix,
    which is the losing side of that problem. Components are validated instead."""
    with pytest.raises(evidence.ArtifactStoreError, match="not a uuid"):
        evidence.artifact_key(
            workspace_id=bad,
            run_id=str(uuid.UUID(int=1)),
            attempt_id=str(uuid.UUID(int=2)),
            kind="SPEECH_TRANSCRIPT",
            content_digest="a" * 64,
        )


def test_the_digest_is_part_of_the_key_so_different_bytes_cannot_overwrite(db: str) -> None:
    a = evidence.artifact_key(
        workspace_id=WS,
        run_id=str(uuid.UUID(int=1)),
        attempt_id=str(uuid.UUID(int=2)),
        kind="SPEECH_TRANSCRIPT",
        content_digest="a" * 64,
    )
    b = evidence.artifact_key(
        workspace_id=WS,
        run_id=str(uuid.UUID(int=1)),
        attempt_id=str(uuid.UUID(int=2)),
        kind="SPEECH_TRANSCRIPT",
        content_digest="b" * 64,
    )
    assert a != b


# --- redaction and retention ---------------------------------------------------------------------


def test_a_redacted_view_is_a_separate_object_with_its_own_digest(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Not a flag on the row.

    One digest describing two different byte streams would make any later verification check the
    wrong one, and an export citing "the transcript" would be ambiguous about which it meant.
    """
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    redacted = b'{"phrases": ["Email, invalid entry"], "redacted": true}'

    with workspace_connection(db, WS) as conn:
        redacted_digest = evidence.attach_redacted_view(
            conn,
            store,
            artifact_id=artifact.artifact_id,
            redacted_payload=redacted,
            content_type="application/json",
            reason="removed a value that looked personal",
        )
        row = conn.execute(
            "SELECT content_digest, redacted_digest, redacted_object_key, retention, "
            "retention_reason FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()

    assert row is not None
    assert redacted_digest != str(row["content_digest"])
    assert str(row["redacted_digest"]) == redacted_digest
    assert str(row["retention"]) == "REDACTED"
    assert row["retention_reason"]
    assert store.get(key=str(row["redacted_object_key"])) == redacted


def test_a_redaction_that_changed_nothing_is_refused(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Recording it would claim a redaction that did not happen."""
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(evidence.ArtifactError, match="byte-identical"):
            evidence.attach_redacted_view(
                conn,
                store,
                artifact_id=artifact.artifact_id,
                redacted_payload=TRANSCRIPT,
                content_type="application/json",
                reason="nothing to redact",
            )


def test_deleting_the_bytes_keeps_the_record_saying_so(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """INV-15 in one test.

    Deleting the row would leave an export with no idea the artifact existed, reporting a complete
    evidence set for a run whose evidence is gone.
    """
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        evidence.promote(
            conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
        )
        evidence.delete_artifact_bytes(
            conn, store, artifact_id=artifact.artifact_id, reason="subject request"
        )
        row = conn.execute(
            "SELECT retention, retention_reason, state FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()

    assert row is not None, "the record must outlive the bytes"
    assert str(row["retention"]) == "DELETED"
    assert str(row["retention_reason"]) == "subject request"
    assert store.exists(key=artifact.object_key) is False


def test_a_deleted_required_artifact_counts_as_missing(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The other half of INV-15: the deletion has to change the completeness answer."""
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        evidence.declare_required_artifacts(
            conn,
            workspace_id=WS,
            run_id=run_id,
            requirements={"SPEECH_TRANSCRIPT": SUPERVISOR},
        )
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        evidence.promote(
            conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
        )
        assert evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id) == []

        evidence.delete_artifact_bytes(
            conn, store, artifact_id=artifact.artifact_id, reason="retention policy"
        )
        missing = evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id)
    assert missing == [
        {"kind": "SPEECH_TRANSCRIPT", "producer": SUPERVISOR, "reason": "BYTES_DELETED"}
    ]


def test_missing_reasons_distinguish_never_uploaded_from_not_promoted(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Three ways to be missing that send an operator somewhere different."""
    run_id, attempt_id = _run_and_attempt(db)
    with workspace_connection(db, WS) as conn:
        evidence.declare_required_artifacts(
            conn,
            workspace_id=WS,
            run_id=run_id,
            requirements={"SPEECH_TRANSCRIPT": SUPERVISOR, "ACTION_TRACE": SUPERVISOR},
        )
        missing = evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id)
    assert {m["reason"] for m in missing} == {"NEVER_UPLOADED"}

    _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        missing = evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id)
    by_kind = {m["kind"]: m["reason"] for m in missing}
    assert by_kind["SPEECH_TRANSCRIPT"] == "STATE_QUARANTINED"
    assert by_kind["ACTION_TRACE"] == "NEVER_UPLOADED"


# --- tenancy -------------------------------------------------------------------------------------


def test_artifacts_are_workspace_isolated(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS_OTHER) as conn:
        assert conn.execute("SELECT 1 FROM evidence_artifact").fetchall() == []
        assert conn.execute("SELECT 1 FROM required_artifact").fetchall() == []


def test_another_tenant_cannot_promote_this_tenants_artifact(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    run_id, attempt_id = _run_and_attempt(db)
    artifact = _upload(db, store, run_id, attempt_id)
    with workspace_connection(db, WS_OTHER) as conn:
        with pytest.raises(evidence.ArtifactError, match="no such artifact"):
            evidence.promote(
                conn, store, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
            )
