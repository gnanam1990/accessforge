"""Deleting evidence, and proving what the deletion did not reach.

FR-020's acceptance has three clauses and the tests here are organised around them:
"deletion invalidates affected completeness claims", "Reports explicitly state any retained audit
metadata, backup delay and user-managed downloaded-copy limits", and the limit stated in the
requirement itself — "local downloaded copies cannot be remotely recalled".

The test that justifies the design is `deleting evidence makes a run incomplete`. A run with less
evidence looks tidier, and an implementation that deleted the rows rather than the bytes would make
a deleted-evidence run report a *complete* evidence set. The tombstone is what prevents that, and
finalization counting a DELETED required artifact as missing is what makes deletion cost something.

Against a real object store, because the one thing a fake cannot prove is that the bytes are gone.

Requirements: FR-020. Invariants: INV-06, INV-15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    deletion,
    evidence,
    migrate,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence.evidence import objectstore

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x360))
WS_OTHER = str(uuid.UUID(int=0x361))
OPERATOR = str(uuid.UUID(int=0x362))
MANIFEST = digest({"m": "fr020"})
SUPERVISOR = "supervisor:mac-01"
TRANSCRIPT = b'{"phrases": ["Email, invalid entry"]}'
SCREENSHOT = b"\x89PNG\r\n\x1a\nnot-really-a-png"


@pytest.fixture(scope="session")
def store() -> evidence.S3ArtifactStore:
    """The real object store, or a failed test saying it is absent.

    Not a skip. "The bytes are gone" is the claim this whole suite makes, and a filesystem stand-in
    would let every assertion pass while proving none of it.
    """
    endpoint = os.environ.get("OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.fail(
            "OBJECT_STORE_ENDPOINT is not configured. These tests assert that deleted bytes are "
            "actually gone from a store, which cannot be shown without one."
        )
    s3 = evidence.S3ArtifactStore(
        evidence.S3Settings(
            endpoint_url=endpoint,
            access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
            secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
            bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        )
    )
    s3.ensure_bucket()
    return s3


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'operator@example.test')", (OPERATOR,)
        )
    yield test_database_url


def _run(url: str, workspace: str = WS) -> tuple[str, str]:
    with workspace_connection(url, workspace) as conn:
        run_id = runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=workspace, lease_epoch=1)
    return run_id, attempt_id


def _promoted(
    url: str,
    s3: evidence.S3ArtifactStore,
    run_id: str,
    attempt_id: str,
    *,
    kind: str = "SPEECH_TRANSCRIPT",
    payload: bytes = TRANSCRIPT,
    content_type: str = "application/json",
    workspace: str = WS,
) -> evidence.StoredArtifact:
    with workspace_connection(url, workspace) as conn:
        stored = evidence.upload_to_quarantine(
            conn,
            s3,
            workspace_id=workspace,
            run_id=run_id,
            attempt_id=attempt_id,
            kind=kind,
            producer_id=SUPERVISOR,
            lease_epoch=1,
            manifest_digest=MANIFEST,
            content_type=content_type,
            payload=payload,
        )
        evidence.promote(
            conn, s3, artifact_id=stored.artifact_id, expected_manifest_digest=MANIFEST
        )
    return stored


def _observation(url: str, run_id: str, attempt_id: str, *, sequence: int = 1) -> None:
    with workspace_connection(url, WS) as conn:
        sequencer.admit_record(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            lease_epoch=1,
            producer_id="observer-1",
            source_record_id=f"evidence-{sequence}",
            producer_sequence=sequence,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"phrase": "Email, invalid entry"},
            source_time=datetime(2026, 9, 11, 12, tzinfo=UTC),
        )


def _delete(
    url: str,
    s3: evidence.S3ArtifactStore,
    run_id: str,
    *,
    classes: tuple[str, ...] = ("READER_SPEECH",),
    attempt_id: str | None = None,
    reason: str = "the customer withdrew consent for captured speech",
) -> deletion.DeletionReport:
    # Two connections, deliberately. Phase one commits when its block exits; only then does phase
    # two touch the store. Doing both on one connection would purge bytes for a deletion that could
    # still roll back, which is the exact failure this split exists to prevent -- so the helper
    # every test goes through enforces the order rather than leaving each test to remember it.
    with workspace_connection(url, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=classes,
            attempt_id=attempt_id,
            reason=reason,
            requested_by=OPERATOR,
        )
    with workspace_connection(url, WS) as conn:
        return report.with_purge(
            deletion.purge_pending_objects(conn, s3, deletion_id=report.deletion_id)
        )


# --- the bytes are actually gone -----------------------------------------------------------------


def test_the_bytes_are_removed_from_the_store(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    assert store.get(key=artifact.object_key) == TRANSCRIPT

    _delete(db, store, run_id)

    # Read back from the store itself, not from the row describing it.
    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=artifact.object_key)


def test_a_tombstone_row_survives_the_bytes(db: str, store: evidence.S3ArtifactStore) -> None:
    """INV-15. Deleting the row instead would leave an export with no idea the artifact existed.

    It would then report a complete evidence set for a run whose evidence is gone, which is the
    single most damaging thing this product could do.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    report = _delete(db, store, run_id)

    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT retention, retention_reason FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()
    assert row is not None
    assert row["retention"] == "DELETED"
    assert "withdrew consent" in row["retention_reason"]
    assert report.artifact_tombstones_kept == 1


def test_a_redacted_view_goes_with_the_original(db: str, store: evidence.S3ArtifactStore) -> None:
    """A derived object. Keeping it would mean removing the original and retaining a masked copy.

    Which is still the thing somebody asked to have removed.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        evidence.attach_redacted_view(
            conn,
            store,
            artifact_id=artifact.artifact_id,
            redacted_payload=b'{"phrases": ["Email, [REDACTED]"]}',
            content_type="application/json",
            reason="a fixture value appeared in the transcript",
        )
        redacted_key = conn.execute(
            "SELECT redacted_object_key FROM evidence_artifact WHERE id = %s",
            (artifact.artifact_id,),
        ).fetchone()
    assert redacted_key is not None and redacted_key["redacted_object_key"] is not None

    _delete(db, store, run_id)

    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=str(redacted_key["redacted_object_key"]))


# --- the completeness claim ----------------------------------------------------------------------


def test_deleting_evidence_makes_a_run_incomplete(db: str, store: evidence.S3ArtifactStore) -> None:
    """The test this design exists for.

    A run with less evidence looks tidier. An implementation that deleted rows rather than bytes
    would make a deleted-evidence run report a *complete* set, and the deletion would silently
    improve the run's standing. Finalization counts a DELETED required artifact as missing, and the
    reason says which of the three kinds of missing it is.
    """
    run_id, attempt_id = _run(db)
    with workspace_connection(db, WS) as conn:
        evidence.declare_required_artifacts(
            conn,
            workspace_id=WS,
            run_id=run_id,
            requirements={"SPEECH_TRANSCRIPT": SUPERVISOR},
        )
    _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        assert evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id) == []

    report = _delete(db, store, run_id)
    assert report.completeness_invalidated is True

    with workspace_connection(db, WS) as conn:
        missing = evidence.missing_required_artifacts(conn, run_id=run_id, attempt_id=attempt_id)
    assert [m["kind"] for m in missing] == ["SPEECH_TRANSCRIPT"]
    # Not "never uploaded". Deliberately removed and never captured are different facts.
    assert missing[0]["reason"] == "BYTES_DELETED"


def test_a_class_that_breaks_nothing_says_so(db: str, store: evidence.S3ArtifactStore) -> None:
    """Reported from the policy, not guessed by the caller.

    A screen recording is supplementary by definition, and deleting one costs a reader context
    rather than proof. Saying otherwise would make every deletion look equally grave and train
    people to ignore the warning that matters.
    """
    run_id, attempt_id = _run(db)
    _promoted(
        db,
        store,
        run_id,
        attempt_id,
        kind="SCREENSHOT",
        payload=SCREENSHOT,
        content_type="image/png",
    )

    report = _delete(db, store, run_id, classes=("SCREEN_RECORDING",))
    assert report.artifact_bytes_deleted == 1
    assert report.completeness_invalidated is False
    assert "No completeness claim depended on these classes" in report.summary


# --- the event chain -----------------------------------------------------------------------------


def test_the_hash_chain_survives_a_cleared_payload(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The content goes; the digest and the chain link stay.

    Deleting the rows would break the chain at the deletion point and make every *undeleted* event
    after it unverifiable -- destroying the evidence somebody chose to keep as collateral damage of
    removing the evidence they chose to delete.
    """
    run_id, attempt_id = _run(db)
    _observation(db, run_id, attempt_id, sequence=1)
    _observation(db, run_id, attempt_id, sequence=2)

    with workspace_connection(db, WS) as conn:
        before = conn.execute(
            "SELECT sequence, payload_digest, previous_event_hash, payload "
            "FROM canonical_event WHERE run_id = %s ORDER BY sequence",
            (run_id,),
        ).fetchall()
    assert len(before) == 2
    assert all("phrase" in str(row["payload"]) for row in before)

    report = _delete(db, store, run_id)
    assert report.event_payloads_cleared == 2

    with workspace_connection(db, WS) as conn:
        after = conn.execute(
            "SELECT sequence, payload_digest, previous_event_hash, payload "
            "FROM canonical_event WHERE run_id = %s ORDER BY sequence",
            (run_id,),
        ).fetchall()

    # Same rows, same sequence, same digests, same chain links.
    assert [r["sequence"] for r in after] == [r["sequence"] for r in before]
    assert [r["payload_digest"] for r in after] == [r["payload_digest"] for r in before]
    assert [r["previous_event_hash"] for r in after] == [r["previous_event_hash"] for r in before]
    # And the content is gone, replaced by something that says so.
    for row in after:
        assert "phrase" not in str(row["payload"])
        assert row["payload"]["deleted"] is True
        assert "chain still verifies" in row["payload"]["meaning"]


def test_structural_events_are_never_cleared(db: str, store: evidence.S3ArtifactStore) -> None:
    """A chain missing its own boundaries could not be read at all."""
    assert deletion.EVENT_CLASS["RUN_STARTED"] == "STRUCTURAL"
    assert deletion.EVENT_CLASS["RUN_FINISHED"] == "STRUCTURAL"
    # And STRUCTURAL is not a retention class, so it can never be named in a deletion.
    from accessforge_persistence.retention import CLASS_DEFINITIONS

    assert "STRUCTURAL" not in CLASS_DEFINITIONS


# --- the report says what it could not reach -----------------------------------------------------


def test_the_report_always_names_what_survived(db: str, store: evidence.S3ArtifactStore) -> None:
    """FR-020's acceptance, clause by clause.

    A boolean return would let a caller say "deleted" and mean it. The one thing nobody may say
    after this operation is that the data is gone without qualification.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    report = _delete(db, store, run_id)

    joined = " ".join(report.retained)
    assert report.retained, "a deletion that reached everything does not exist"
    assert "Audit metadata is retained" in joined
    assert "Backups taken in the last 35 day(s)" in joined
    assert "hash chain is intact" in joined
    assert "Human review records were not included" in joined


def test_the_report_counts_exports_and_downloads(db: str, store: evidence.S3ArtifactStore) -> None:
    """ "Somebody may hold a copy" and "four people downloaded it" are different conversations."""
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        export_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO evidence_export
                (id, workspace_id, run_id, attempt_id, requested_by, bundle_digest, trust_level,
                 signing_key_id, retention_snapshot, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,'LIMITED_DISCLOSURE','af-unsigned-local','{}',
                    now() + interval '7 days')
            """,
            (export_id, WS, run_id, attempt_id, OPERATOR, digest({"b": 1})),
        )
        for _ in range(2):
            conn.execute(
                "INSERT INTO evidence_export_download (id, workspace_id, export_id, downloaded_by) "
                "VALUES (%s,%s,%s,%s)",
                (str(uuid.uuid4()), WS, export_id, OPERATOR),
            )

    report = _delete(db, store, run_id)
    joined = " ".join(report.retained)
    assert "1 export(s) of this run were built and downloaded 2 time(s)" in joined
    assert "cannot be recalled" in joined


def test_a_run_nobody_exported_says_that_instead(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    report = _delete(db, store, run_id)
    assert "No export of this run was ever built" in " ".join(report.retained)


# --- the deletion records itself -----------------------------------------------------------------


def test_the_deletion_is_recorded_and_readable(db: str, store: evidence.S3ArtifactStore) -> None:
    """The one thing a deletion does not delete.

    An erasure nobody can prove is worse than none: the data may be gone and the organisation still
    cannot say so.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    report = _delete(db, store, run_id)

    with workspace_connection(db, WS) as conn:
        records = deletion.deletions_for_run(conn, run_id=run_id)
    assert len(records) == 1
    assert str(records[0]["id"]) == report.deletion_id
    assert list(records[0]["evidence_classes"]) == ["READER_SPEECH"]
    assert "withdrew consent" in records[0]["reason"]
    assert str(records[0]["requested_by"]) == OPERATOR
    assert records[0]["completeness_invalidated"] is True


def test_a_deletion_with_no_reason_is_refused(db: str, store: evidence.S3ArtifactStore) -> None:
    run_id, _ = _run(db)
    with pytest.raises(deletion.DeletionError, match="state a reason"):
        _delete(db, store, run_id, reason="   ")


def test_a_deletion_naming_no_class_is_refused(db: str, store: evidence.S3ArtifactStore) -> None:
    """A deletion of nothing reports success and removes nothing."""
    run_id, _ = _run(db)
    with pytest.raises(deletion.DeletionError, match="at least one evidence class"):
        _delete(db, store, run_id, classes=())


def test_an_unknown_class_is_refused_rather_than_matching_nothing(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """A typo would otherwise report a successful deletion that removed nothing at all."""
    run_id, _ = _run(db)
    with pytest.raises(deletion.UnknownEvidenceClass, match="READER_SPEACH"):
        _delete(db, store, run_id, classes=("READER_SPEACH",))


# --- scope ---------------------------------------------------------------------------------------


def test_deleting_one_attempt_leaves_the_other_alone(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    run_id, first = _run(db)
    with workspace_connection(db, WS) as conn:
        second = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=2)

    kept = _promoted(db, store, run_id, second)
    removed = _promoted(db, store, run_id, first)

    _delete(db, store, run_id, attempt_id=first)

    assert store.get(key=kept.object_key) == TRANSCRIPT
    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=removed.object_key)


def test_a_run_in_another_workspace_is_untouched(db: str, store: evidence.S3ArtifactStore) -> None:
    """Row-level security does the work; this asserts the consequence.

    A deletion is the operation where a scope mistake is least recoverable, so the boundary is
    tested rather than assumed.
    """
    mine_run, mine_attempt = _run(db)
    theirs_run, theirs_attempt = _run(db, workspace=WS_OTHER)
    mine = _promoted(db, store, mine_run, mine_attempt)
    theirs = _promoted(db, store, theirs_run, theirs_attempt, workspace=WS_OTHER)

    _delete(db, store, mine_run)

    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=mine.object_key)
    assert store.get(key=theirs.object_key) == TRANSCRIPT


def test_deleting_twice_removes_nothing_the_second_time(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Idempotent by tombstone, not by luck.

    The scope query excludes what is already DELETED, so a second pass cannot try to delete an
    object that is gone -- which the store would answer with an error the caller would read as a
    failed deletion.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)

    first = _delete(db, store, run_id)
    second = _delete(db, store, run_id)

    assert first.artifact_bytes_deleted == 1
    assert second.artifact_bytes_deleted == 0
    # Both are recorded. A deletion that removed nothing is still a request somebody made.
    with workspace_connection(db, WS) as conn:
        assert len(deletion.deletions_for_run(conn, run_id=run_id)) == 2


def test_every_artifact_kind_the_schema_permits_has_a_retention_class(db: str) -> None:
    """Read from the database's own CHECK constraint, not from a list in this test.

    An artifact kind added to the schema without a line in `ARTIFACT_CLASS` would otherwise survive
    a deletion that reported success -- the exact failure the whole operation exists to avoid. The
    constraint is the authority on what kinds exist, so asking it is the only way this guard cannot
    quietly stop covering a kind.
    """
    import re

    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            """
            SELECT pg_get_constraintdef(oid) AS definition
              FROM pg_constraint
             WHERE conname = 'evidence_artifact_kind_check'
            """
        ).fetchone()
    assert row is not None, "the kind constraint is gone; this guard would pass vacuously"

    kinds = set(re.findall(r"'([A-Z_]+)'::text", str(row["definition"])))
    assert len(kinds) >= 7, f"only found {kinds}; the constraint shape changed"
    unclassified = sorted(kinds - set(deletion.ARTIFACT_CLASS))
    assert not unclassified, (
        f"artifact kind(s) {', '.join(unclassified)} have no retention class. Add a line to "
        "ARTIFACT_CLASS with a comment saying why that class, rather than letting a deletion skip "
        "them and report success."
    )


def test_every_event_type_the_contract_names_has_a_class() -> None:
    """The same guard for the canonical event vocabulary, read from the generated contract."""
    from accessforge_contracts._generated import EVENT_TYPES

    unclassified = sorted(set(EVENT_TYPES) - set(deletion.EVENT_CLASS))
    assert not unclassified, (
        f"event type(s) {', '.join(unclassified)} have no class. An unclassified payload survives "
        "every deletion silently."
    )


def test_an_unclassified_kind_in_scope_is_refused_rather_than_skipped(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """And if one ever does slip through the guard above, the deletion refuses rather than skipping.

    Forced by removing a kind from the mapping for the length of this test. Two layers for one
    mistake, because the cost of being wrong here is an artifact somebody believes was deleted.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)

    original = dict(deletion.ARTIFACT_CLASS)
    del deletion.ARTIFACT_CLASS["SPEECH_TRANSCRIPT"]
    try:
        with pytest.raises(deletion.DeletionError, match="has no retention class"):
            _delete(db, store, run_id)
    finally:
        deletion.ARTIFACT_CLASS.clear()
        deletion.ARTIFACT_CLASS.update(original)


# --- the limit the report states, demonstrated ---------------------------------------------------


def test_a_restore_from_a_backup_taken_before_a_deletion_brings_the_evidence_back(
    db: str, store: evidence.S3ArtifactStore, backup_database_url: str
) -> None:
    """The sentence in every report, proved rather than asserted.

    Module 26's handoff recorded this as missing: "no test of restoring a backup that predates a
    deletion". It is the one claim in `retained` that could be quietly false — the others are
    observable in the same database, and this one needs a second database to demonstrate.

    It also states the shape of the problem honestly. The deletion worked. The restore worked. Both
    are correct, and together they mean deleted evidence comes back — which is why the report names
    a window in days rather than saying the data is gone.

    Two things are *not* claimed here. This restores the database, not the object store, so the
    artifact tombstone returns as `PROMOTED` while its bytes stay gone; a real restore replays the
    objects too, which module 27's drill covers. And the deletion record returns with it, so a
    restored database still says the deletion was requested — the evidence is back and the fact that
    somebody asked for its removal is not lost.
    """
    import shutil
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    _observation(db, run_id, attempt_id, sequence=1)

    def with_database(url: str, name: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))

    # The backup is taken *before* the deletion, which is the whole point.
    # Resolved absolutely rather than executed by name: a test that ran whatever a manipulated PATH
    # pointed at would be taking a backup with something other than pg_dump.
    pg_dump = shutil.which("pg_dump")
    psql = shutil.which("psql")
    assert pg_dump is not None and psql is not None, "pg_dump and psql must be on PATH"

    dump = subprocess.run(  # noqa: S603 - argv built here, no shell
        [pg_dump, backup_database_url], capture_output=True, text=True, check=False
    )
    assert dump.returncode == 0, dump.stderr[-2000:]

    report = _delete(db, store, run_id)
    assert report.artifact_bytes_deleted == 1
    assert report.event_payloads_cleared == 1
    assert any("Backups taken in the last" in line for line in report.retained)

    # Gone from the live database.
    with workspace_connection(db, WS) as conn:
        live = conn.execute(
            "SELECT retention FROM evidence_artifact WHERE id = %s", (artifact.artifact_id,)
        ).fetchone()
        payload = conn.execute(
            "SELECT payload FROM canonical_event WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert live is not None and live["retention"] == "DELETED"
    assert payload is not None and "phrase" not in str(payload["payload"])

    target = f"accessforge_predeletion_{uuid.uuid4().hex[:8]}"
    from accessforge_persistence import connect

    with connect(with_database(backup_database_url, "postgres")) as admin:
        admin.autocommit = True
        admin.execute(f'CREATE DATABASE "{target}"')  # noqa: S608 - generated name
    try:
        restored_url = with_database(backup_database_url, target)
        restore = subprocess.run(  # noqa: S603 - argv built here, no shell
            [psql, "--quiet", "-v", "ON_ERROR_STOP=1", restored_url],
            input=dump.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, restore.stderr[-2000:]

        with connect(restored_url) as conn:
            back = conn.execute(
                "SELECT retention FROM evidence_artifact WHERE id = %s",
                (artifact.artifact_id,),
            ).fetchone()
            restored_payload = conn.execute(
                "SELECT payload FROM canonical_event WHERE run_id = %s", (run_id,)
            ).fetchone()
            deletions = conn.execute(
                "SELECT count(*) AS n FROM evidence_deletion WHERE run_id = %s", (run_id,)
            ).fetchone()

        # The content is back, and the tombstone is not. `retention` is the column deletion moves
        # (RETAINED -> DELETED); `state` is the intake lifecycle (QUARANTINED -> PROMOTED) and is
        # untouched by a deletion. Asserting the wrong one of those two is how a test convinces
        # itself a restore worked.
        assert back is not None and back["retention"] == "RETAINED"
        assert restored_payload is not None
        assert "phrase" in str(restored_payload["payload"])

        # And the backup predates the deletion, so it does not know one happened. That asymmetry is
        # the reason a restore has to be reconciled before it is served, and why this report names a
        # window instead of claiming the data is gone.
        assert deletions is not None and int(deletions["n"]) == 0
    finally:
        with connect(with_database(backup_database_url, "postgres")) as admin:
            admin.autocommit = True
            admin.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')  # noqa: S608


# --- the two phases, and the window between them --------------------------------------------------


class _Interrupted(Exception):
    """Whatever ends a request between marking the rows and committing them."""


def test_a_rollback_before_the_commit_leaves_the_bytes_and_the_record_agreeing(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The failure the two-phase split exists to prevent.

    Deleting objects inside the transaction means a rollback can leave the bytes gone, the artifact
    still RETAINED and no deletion record at all -- a run reporting a *complete* evidence set whose
    evidence no longer exists. Nothing about that state looks wrong to a reader, which is what makes
    it the worst outcome this feature has.

    So the order is fixed: the database commits first, and the only reachable window is the harmless
    one, where the record says deleted and some bytes are still queued for removal.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)

    with pytest.raises(_Interrupted):
        with workspace_connection(db, WS) as conn:
            deletion.record_deletion(
                conn,
                workspace_id=WS,
                run_id=run_id,
                classes=("READER_SPEECH",),
                reason="the customer withdrew consent for captured speech",
                requested_by=OPERATOR,
            )
            raise _Interrupted

    # The bytes are still there, which is the point: phase one promised nothing that survived.
    assert store.get(key=artifact.object_key) == TRANSCRIPT
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT retention FROM evidence_artifact WHERE id = %s", (artifact.artifact_id,)
        ).fetchone()
        assert row is not None and row["retention"] == "RETAINED"
        assert deletion.deletions_for_run(conn, run_id=run_id) == []
        assert deletion.pending_purges(conn) == 0


def test_a_purge_that_stops_at_its_limit_reports_the_backlog(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """A pass where everything it tried succeeded is not a pass that emptied the queue.

    `limit` caps what one pass looks at, so counting only this pass's failures would report a
    backlog of untouched keys as zero -- a report saying the store released everything while it
    still holds the bytes.
    """
    run_id, attempt_id = _run(db)
    first = _promoted(db, store, run_id, attempt_id)
    second = _promoted(db, store, run_id, attempt_id, payload=TRANSCRIPT + b" second")

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    assert report.objects_enqueued == 2

    with workspace_connection(db, WS) as conn:
        partial = report.with_purge(
            deletion.purge_pending_objects(conn, store, deletion_id=report.deletion_id, limit=1)
        )
    assert partial.objects_purged == 1
    assert partial.objects_still_present == 1
    assert any("has not released them yet" in line for line in partial.retained)

    with workspace_connection(db, WS) as conn:
        finished = partial.with_purge(
            deletion.purge_pending_objects(conn, store, deletion_id=report.deletion_id)
        )
    assert finished.objects_purged == 2
    assert finished.objects_still_present == 0
    assert not any("has not released them yet" in line for line in finished.retained)
    for artifact in (first, second):
        with pytest.raises(objectstore.ArtifactStoreError):
            store.get(key=artifact.object_key)


class _RefusingStore:
    """An object store that is down, which is a delay and not a failed deletion."""

    def delete(self, *, key: str) -> None:
        raise RuntimeError(f"connection refused while deleting {key}")


def test_a_store_outage_keeps_the_key_pending_instead_of_raising(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The database has already committed saying this evidence is deleted.

    Raising here would tell a caller the deletion failed when what actually happened is that some
    bytes are queued. The error is recorded on the row, the count stays honest, and the next pass
    finishes the job.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )

    with workspace_connection(db, WS) as conn:
        outage = report.with_purge(
            deletion.purge_pending_objects(conn, _RefusingStore(), deletion_id=report.deletion_id)
        )
    assert outage.objects_purged == 0
    assert outage.objects_still_present == 1
    assert any("has not released them yet" in line for line in outage.retained)
    assert store.get(key=artifact.object_key) == TRANSCRIPT

    with workspace_connection(db, WS) as conn:
        failure = conn.execute(
            "SELECT attempts, last_error FROM evidence_object_purge WHERE deletion_id = %s",
            (report.deletion_id,),
        ).fetchone()
        assert failure is not None
        assert failure["attempts"] == 1
        assert "connection refused" in failure["last_error"]

        # And a retry finishes it, which is what makes an outage a delay.
        retried = outage.with_purge(
            deletion.purge_pending_objects(conn, store, deletion_id=report.deletion_id)
        )
    assert retried.objects_purged == 1
    assert retried.objects_still_present == 0
    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=artifact.object_key)


def test_a_purge_refuses_a_deletion_it_cannot_see_rather_than_finding_nothing(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """An empty queue and an invisible one are the same answer and opposite facts.

    This is the bug the two-phase split invites: purge before phase one commits, and the queue looks
    empty. Every count comes back zero, the report says the store released everything, and the bytes
    are still there. It reads as a clean deletion, which is why it has to raise.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as uncommitted:
        report = deletion.record_deletion(
            uncommitted,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
        # A second session, which cannot see the still-open transaction above.
        with workspace_connection(db, WS) as other:
            with pytest.raises(deletion.DeletionError, match="not visible on this connection"):
                deletion.purge_pending_objects(other, store, deletion_id=report.deletion_id)

    assert store.get(key=artifact.object_key) == TRANSCRIPT


def test_a_report_refuses_a_count_measured_for_another_deletion(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """A sweep counts every deletion's backlog, and this one may only quote its own.

    Folding a sweep's number in would let one deletion report bytes as released on another's work,
    or invent objects it never enqueued.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )

    with workspace_connection(db, WS) as conn:
        sweep = deletion.purge_pending_objects(conn, store)
    assert sweep.scope is None
    with pytest.raises(deletion.DeletionError, match="may only quote a count taken for itself"):
        report.with_purge(sweep)


# --- through the HTTP surface ---------------------------------------------------------------------


@pytest.fixture()
def api(db: str) -> Iterator[object]:
    """The real application, with a signed-in owner and a member who is not one."""
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OPERATOR),
        )
    with TestClient(create_app(settings)) as client:
        yield client


def _sign_in(db: str, client: object, user_id: str = OPERATOR) -> str:
    from accessforge_api.auth import SESSION_COOKIE, issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)  # type: ignore[attr-defined]
    return issued.csrf_token


def test_the_route_reports_what_it_could_not_reach(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """A 201 here means the deletion was performed, not that the data is gone.

    `retained` is the deliverable. A route answering `{"deleted": true}` would be the most dangerous
    endpoint in this product.
    """
    from accessforge_api.auth import CSRF_HEADER

    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    csrf = _sign_in(db, api)

    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={
            "evidenceClasses": ["READER_SPEECH"],
            "reason": "the customer withdrew consent for captured speech",
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["artifactsMarkedDeleted"] == 1
    assert body["completenessInvalidated"] is True
    assert body["retained"], "a deletion that reached everything does not exist"
    assert any("Audit metadata is retained" in line for line in body["retained"])
    assert any("Backups taken in the last" in line for line in body["retained"])
    assert "does not mean the data is gone" in body["meaning"]
    # Counted, not promised. The route reports what the store actually released, so a reader can
    # tell "recorded as deleted" from "erased" -- which is the distinction whoever quotes this to a
    # regulator depends on.
    assert body["objectsPurged"] == body["objectsEnqueued"] > 0
    assert body["objectsStillPresent"] == 0
    assert not any("has not released them yet" in line for line in body["retained"])


def test_the_route_refuses_a_bare_string_of_classes(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """A string is iterable, so it would be read as one class per character — matching nothing."""
    from accessforge_api.auth import CSRF_HEADER

    run_id, _ = _run(db)
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={"evidenceClasses": "READER_SPEECH", "reason": "a reason"},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "must be an array" in response.json()["detail"]


def test_the_route_requires_a_reason(db: str, store: evidence.S3ArtifactStore, api: object) -> None:
    from accessforge_api.auth import CSRF_HEADER

    run_id, _ = _run(db)
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={"evidenceClasses": ["DIAGNOSTIC"]},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "reason is required" in response.json()["detail"]


def test_a_member_who_is_not_an_owner_cannot_delete_evidence(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """`WORKSPACE_CONFIGURE`, which is owner-only.

    Deleting evidence is irreversible within this system and invalidates completeness claims other
    people's reviews may rest on. The permission to request a run is not the permission to destroy
    its evidence.
    """
    from accessforge_api.auth import CSRF_HEADER

    maintainer = str(uuid.UUID(int=0x363))
    with unscoped_connection(db) as conn:
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'maintainer@example.test')", (maintainer,)
        )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s,%s,'MAINTAINER')",
            (WS, maintainer),
        )

    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    csrf = _sign_in(db, api, user_id=maintainer)

    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={"evidenceClasses": ["READER_SPEECH"], "reason": "tidying up"},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    # And nothing was removed.
    assert store.get(key=artifact.object_key) == TRANSCRIPT


def test_a_run_in_another_workspace_answers_not_found_rather_than_deleting_nothing(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """A report saying nothing was deleted would read as success."""
    from accessforge_api.auth import CSRF_HEADER

    theirs, _ = _run(db, workspace=WS_OTHER)
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{theirs}/deletions",
        json={"evidenceClasses": ["READER_SPEECH"], "reason": "a reason"},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_the_listing_distinguishes_deleted_from_never_captured(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    from accessforge_api.auth import CSRF_HEADER

    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    csrf = _sign_in(db, api)
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={"evidenceClasses": ["READER_SPEECH"], "reason": "consent withdrawn"},
        headers={CSRF_HEADER: csrf},
    )

    listed = api.get(f"/v1/workspaces/{WS}/runs/{run_id}/deletions")  # type: ignore[attr-defined]
    assert listed.status_code == 200
    body = listed.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["evidenceClasses"] == ["READER_SPEECH"]
    assert body["items"][0]["reason"] == "consent withdrawn"
    assert "different fact from evidence that was never captured" in body["meaning"]


def test_the_route_removes_the_bytes_after_it_answers(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """Through the route, the bytes really go — and only after the record of it is committed.

    The report is honest either way, so the one thing it cannot prove about itself is that anything
    ran. That assertion belongs on the store.

    The purge runs on its own connection because this request's transaction has not committed while
    the endpoint is executing: purging there would delete bytes for a deletion that could still roll
    back, leaving the artifact RETAINED with its evidence gone.
    """
    from accessforge_api.auth import CSRF_HEADER

    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)
    csrf = _sign_in(db, api)

    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions",
        json={
            "evidenceClasses": ["READER_SPEECH"],
            "reason": "the customer withdrew consent for captured speech",
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["objectsPurged"] == 1
    assert response.json()["objectsStillPresent"] == 0

    # Gone from the store, not merely marked in a row.
    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=artifact.object_key)

    # And the listing says so, read from the queue rather than repeated from the 201. This is the
    # field an operator checks when they need to know whether the bytes actually went.
    listing = api.get(f"/v1/workspaces/{WS}/runs/{run_id}/deletions")  # type: ignore[attr-defined]
    assert listing.status_code == 200, listing.text
    records = listing.json()["items"]
    assert len(records) == 1
    assert records[0]["objectsStillPresent"] == 0
    assert records[0]["artifactsMarkedDeleted"] == 1


def test_a_backlog_longer_than_one_pass_is_drained_rather_than_left(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """One pass takes at most `limit` keys, and nothing re-enqueues what it leaves behind.

    The scope query skips artifacts already marked DELETED, so requesting the same deletion again
    queues nothing. A single pass would strand every key past the limit with no path back to them.
    """
    run_id, attempt_id = _run(db)
    artifacts = [
        _promoted(db, store, run_id, attempt_id, payload=TRANSCRIPT + str(n).encode())
        for n in range(3)
    ]

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    assert report.objects_enqueued == 3

    with workspace_connection(db, WS) as conn:
        # One key per pass, so a single pass would leave two behind.
        drained = report.with_purge(
            deletion.purge_until_drained(conn, store, deletion_id=report.deletion_id, limit=1)
        )
    assert drained.objects_purged == 3
    assert drained.objects_still_present == 0
    for artifact in artifacts:
        with pytest.raises(objectstore.ArtifactStoreError):
            store.get(key=artifact.object_key)


def test_a_store_that_is_down_stops_the_drain_instead_of_spinning(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """A pass that removes nothing is an outage, and repeating it changes nothing but the count."""
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    with workspace_connection(db, WS) as conn:
        outcome = deletion.purge_until_drained(
            conn, _RefusingStore(), deletion_id=report.deletion_id
        )
        attempts = conn.execute(
            "SELECT attempts FROM evidence_object_purge WHERE deletion_id = %s",
            (report.deletion_id,),
        ).fetchone()

    assert outcome.purged == 0
    assert outcome.still_pending == 1
    # One attempt, not fifty. The loop stops on the first pass that removes nothing, so an outage
    # costs one store call rather than hammering a service that is already struggling.
    assert attempts is not None and attempts["attempts"] == 1


def test_an_operator_can_finish_a_deletion_the_store_could_not(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """The route the report names. Without it, `objectsStillPresent` never reaches zero.

    The artifacts are already DELETED after the first attempt, so requesting the same deletion
    again marks nothing and queues nothing -- the stranded keys have no other way back.
    """
    from accessforge_api.auth import CSRF_HEADER

    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)

    # The original deletion, with the object store unreachable.
    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    with workspace_connection(db, WS) as conn:
        deletion.purge_until_drained(conn, _RefusingStore(), deletion_id=report.deletion_id)
    assert store.get(key=artifact.object_key) == TRANSCRIPT

    csrf = _sign_in(db, api)
    listing = api.get(f"/v1/workspaces/{WS}/runs/{run_id}/deletions")  # type: ignore[attr-defined]
    assert listing.json()["items"][0]["objectsStillPresent"] == 1

    retry = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions/{report.deletion_id}/retry",
        headers={CSRF_HEADER: csrf},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["objectsPurged"] == 1
    assert retry.json()["objectsStillPresent"] == 0

    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=artifact.object_key)
    again = api.get(f"/v1/workspaces/{WS}/runs/{run_id}/deletions")  # type: ignore[attr-defined]
    assert again.json()["items"][0]["objectsStillPresent"] == 0


def test_a_retry_for_a_deletion_belonging_to_another_run_is_not_found(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """Evidence destroyed under a URL naming something else is not what the caller agreed to."""
    from accessforge_api.auth import CSRF_HEADER

    mine, attempt = _run(db)
    _promoted(db, store, mine, attempt)
    other, _ = _run(db)

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=mine,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{other}/deletions/{report.deletion_id}/retry",
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 404, response.text


def test_a_maintainer_who_is_not_an_owner_cannot_retry_a_purge(
    db: str, store: evidence.S3ArtifactStore, api: object
) -> None:
    """It finishes destroying evidence. Reading a run is not permission to do that."""
    from accessforge_api.auth import CSRF_HEADER

    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )

    maintainer = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, %s)",
            (maintainer, f"{maintainer}@example.test"),
        )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'MAINTAINER')",
            (WS, maintainer),
        )
    csrf = _sign_in(db, api, user_id=maintainer)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs/{run_id}/deletions/{report.deletion_id}/retry",
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 403, response.text


def test_an_unclassified_event_type_in_scope_is_refused_rather_than_skipped(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """`canonical_event.event_type` has no CHECK, so nothing else catches this.

    The artifact path already refuses an unmapped kind. Without the same answer here, an event type
    added later without a retention class would not be refused -- it would just be absent from the
    list of types to clear, and its payload would survive a deletion that reported success.
    """
    run_id, attempt_id = _run(db)
    _promoted(db, store, run_id, attempt_id)
    _observation(db, run_id, attempt_id)

    # Written past the sequencer deliberately: the point is an event type the mapping does not
    # know, which is what a future migration adding one looks like from here.
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE canonical_event SET event_type = 'FUTURE_THING' WHERE run_id = %s", (run_id,)
        )

    with pytest.raises(deletion.DeletionError, match="FUTURE_THING"):
        with workspace_connection(db, WS) as conn:
            deletion.record_deletion(
                conn,
                workspace_id=WS,
                run_id=run_id,
                classes=("READER_SPEECH",),
                reason="the customer withdrew consent for captured speech",
                requested_by=OPERATOR,
            )


def test_a_second_purge_skips_rows_another_is_holding_rather_than_double_counting(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """Two callers purging one deletion must not both claim the same key.

    An operator retrying twice, or a retry racing a fresh deletion's drain, would otherwise select
    the same pending rows, both call the store for the same key and both count it -- so
    `objectsPurged` would describe more work than was done. Without SKIP LOCKED the second caller
    blocks instead, waiting on network deletes it cannot see, which is why this asserts against a
    statement timeout: a version that blocks fails here rather than hanging the suite.
    """
    run_id, attempt_id = _run(db)
    artifact = _promoted(db, store, run_id, attempt_id)

    with workspace_connection(db, WS) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=WS,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )

    with workspace_connection(db, WS) as holder:
        # Whatever a concurrent purge looks like mid-flight: the row claimed, the store call not
        # finished, nothing committed.
        held = holder.execute(
            "SELECT id FROM evidence_object_purge WHERE deletion_id = %s FOR UPDATE",
            (report.deletion_id,),
        ).fetchall()
        assert len(held) == 1

        with workspace_connection(db, WS) as other:
            other.execute("SELECT set_config('statement_timeout', '4s', true)")
            outcome = deletion.purge_pending_objects(other, store, deletion_id=report.deletion_id)

        assert outcome.purged == 0, "the second caller claimed a row the first was already holding"
        # Still pending, and truthfully so: as far as this caller can see those bytes are there.
        assert outcome.still_pending == 1

    # And the holder's own claim is intact -- skipping is not stealing.
    assert store.get(key=artifact.object_key) == TRANSCRIPT
    with workspace_connection(db, WS) as conn:
        assert deletion.pending_purges(conn, deletion_id=report.deletion_id) == 1
