"""The worker that finishes deletions the object store could not.

FR-020's report promises that bytes still queued will be taken by a later pass. Until this worker
existed there was no later pass: a deletion whose store was unreachable waited for a person to
notice and call the retry route, and `objectsStillPresent` stayed above zero indefinitely -- the
database reporting evidence as deleted while the store still held it.

The test that justifies the design is `a sweeper that cannot see the queue refuses to run`. The
queue is workspace-scoped under `FORCE ROW LEVEL SECURITY`, so a worker on an ordinary application
role sees an empty queue whatever is in it, reports nothing to do, and exits successfully. A
deletion feature whose worker silently does nothing is worse than one with no worker, because the
first looks finished.

Against a real object store, because the claim is that the bytes are gone.

Requirements: FR-020. Invariants: INV-15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from accessforge_orchestrator.maintenance.purge_worker import sweep_once
from accessforge_persistence import (
    assert_row_level_security_enforced,
    deletion,
    evidence,
    migrate,
    runs,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence.evidence import objectstore

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0x3A0))
WS_B = str(uuid.UUID(int=0x3A1))
OPERATOR = str(uuid.UUID(int=0x3A2))
MANIFEST = "f" * 64
TRANSCRIPT = b'{"phrases": ["Email, invalid entry"]}'


@pytest.fixture(scope="session")
def store() -> evidence.S3ArtifactStore:
    endpoint = os.environ.get("OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.fail(
            "OBJECT_STORE_ENDPOINT is not configured. This suite asserts that a worker actually "
            "removes bytes from a store, which cannot be shown without one."
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
        for ws, name in ((WS_A, "A"), (WS_B, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'operator@example.test')", (OPERATOR,)
        )
    yield test_database_url


class _Down:
    """The object store during an outage."""

    def delete(self, *, key: str) -> None:
        raise RuntimeError(f"connection refused while deleting {key}")


def _queued_deletion(db: str, store: evidence.S3ArtifactStore, workspace: str) -> tuple[str, str]:
    """A committed deletion whose bytes are still in the store, as an outage leaves it."""
    with workspace_connection(db, workspace) as conn:
        run_id = runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=workspace, lease_epoch=1)
    with workspace_connection(db, workspace) as conn:
        stored = evidence.upload_to_quarantine(
            conn,
            store,
            workspace_id=workspace,
            run_id=run_id,
            attempt_id=attempt_id,
            kind="SPEECH_TRANSCRIPT",
            producer_id="supervisor:mac-01",
            lease_epoch=1,
            manifest_digest=MANIFEST,
            content_type="application/json",
            payload=TRANSCRIPT + workspace.encode(),
        )
        evidence.promote(
            conn, store, artifact_id=stored.artifact_id, expected_manifest_digest=MANIFEST
        )
    with workspace_connection(db, workspace) as conn:
        report = deletion.record_deletion(
            conn,
            workspace_id=workspace,
            run_id=run_id,
            classes=("READER_SPEECH",),
            reason="the customer withdrew consent for captured speech",
            requested_by=OPERATOR,
        )
    # The outage: recorded as deleted, bytes untouched.
    with workspace_connection(db, workspace) as conn:
        deletion.purge_pending_objects(conn, _Down(), deletion_id=report.deletion_id)
    assert store.get(key=stored.object_key) == TRANSCRIPT + workspace.encode()
    return report.deletion_id, stored.object_key


def test_a_sweeper_that_cannot_see_the_queue_refuses_to_run(
    db: str, store: evidence.S3ArtifactStore
) -> None:
    """The failure nobody notices, made loud.

    On the application role row-level security hides every other workspace's queue rows, so
    discovery returns an empty list -- and an empty list is indistinguishable from "nothing to do".
    A worker like that reports a clean sweep for ever while the bytes it exists to remove stay in
    the store.
    """
    _queued_deletion(db, store, WS_A)

    with pytest.raises(deletion.DeletionError, match="bypasses row-level security"):
        # `db` is the application role, which is exactly what a careless deployment would hand it.
        sweep_once(db, store, sweep_url=db)

    # And it really would have seen nothing, which is why the guard is not merely defensive.
    with unscoped_connection(db) as conn:
        assert deletion.workspaces_with_pending_purges(conn) == []


def test_a_sweep_finishes_deletions_in_every_workspace_that_has_them(
    db: str, store: evidence.S3ArtifactStore, backup_database_url: str
) -> None:
    """One tick, two tenants, and the bytes actually gone from the store.

    Discovery runs on the maintenance role that can see across workspaces; every deletion is
    performed on a connection scoped to one workspace, under the same row-level security as any
    other write.
    """
    first, key_a = _queued_deletion(db, store, WS_A)
    second, key_b = _queued_deletion(db, store, WS_B)

    report = sweep_once(db, store, sweep_url=backup_database_url)

    assert {sweep.workspace_id for sweep in report.workspaces} == {WS_A, WS_B}
    assert report.purged == 2
    assert report.still_pending == 0
    for key in (key_a, key_b):
        with pytest.raises(objectstore.ArtifactStoreError):
            store.get(key=key)
    for workspace, deletion_id in ((WS_A, first), (WS_B, second)):
        with workspace_connection(db, workspace) as conn:
            assert deletion.pending_purges(conn, deletion_id=deletion_id) == 0


def test_a_sweep_with_nothing_queued_says_so_rather_than_claiming_work(
    db: str, store: evidence.S3ArtifactStore, backup_database_url: str
) -> None:
    """An idle tick is the normal case and must not read as a successful purge of anything."""
    report = sweep_once(db, store, sweep_url=backup_database_url)

    assert report.workspaces == ()
    assert report.purged == 0
    assert "nothing was queued" in report.summary


def test_a_store_still_down_leaves_the_keys_queued_without_raising(
    db: str, store: evidence.S3ArtifactStore, backup_database_url: str
) -> None:
    """A tick during an outage is a delay, not a failed sweep.

    Raising here would end the worker over a condition that resolves itself, and the queue would go
    back to waiting for a human. The error stays recorded against the key and the count stays
    honest.
    """
    deletion_id, key = _queued_deletion(db, store, WS_A)

    report = sweep_once(db, _Down(), sweep_url=backup_database_url)

    assert report.purged == 0
    assert report.still_pending == 1
    assert store.get(key=key) == TRANSCRIPT + WS_A.encode()
    with workspace_connection(db, WS_A) as conn:
        row = conn.execute(
            "SELECT attempts, last_error FROM evidence_object_purge WHERE deletion_id = %s",
            (deletion_id,),
        ).fetchone()
    assert row is not None
    assert int(row["attempts"]) >= 1
    assert "connection refused" in str(row["last_error"])

    # And the next tick, with the store back, finishes it.
    recovered = sweep_once(db, store, sweep_url=backup_database_url, now=datetime.now(UTC))
    assert recovered.purged == 1
    with pytest.raises(objectstore.ArtifactStoreError):
        store.get(key=key)
