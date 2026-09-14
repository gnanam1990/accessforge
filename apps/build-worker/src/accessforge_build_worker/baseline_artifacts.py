"""Create-only baseline captures with bounded reads, retirement tombstones and restore binding."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

import psycopg

from accessforge_persistence import baseline_builds as builds
from accessforge_persistence import workspace_connection

from .artifacts import CandidateArchiveStore, CandidateRetirementStore, _expired, _policy
from .sandbox import SandboxBuild
from .snapshot import MAX_ARCHIVE_BYTES, SourceSnapshot, read_artifact


def archive_key(workspace_id: str, build_id: str, content_digest: str) -> str:
    if (
        str(uuid.UUID(workspace_id)) != workspace_id
        or str(uuid.UUID(build_id)) != build_id
        or not re.fullmatch(r"[a-f0-9]{64}", content_digest)
    ):
        raise builds.Refused("invalid baseline archive identity")
    return f"workspaces/{workspace_id}/baseline-builds/{build_id}/archives/{content_digest}"


def _capture(
    conn: psycopg.Connection[Any], workspace_id: str, build_id: str, *, authorize: bool
) -> dict[str, Any]:
    # Read the immutable run identity first, then follow run-before-build lock ordering.
    row = conn.execute(
        "SELECT run_id FROM baseline_build_attempt WHERE id=%s", (build_id,)
    ).fetchone()
    if row is None:
        raise builds.Refused("baseline capture is unavailable")
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (row["run_id"],))
    capture = conn.execute(
        "SELECT * FROM baseline_build_attempt WHERE id=%s FOR UPDATE", (build_id,)
    ).fetchone()
    if (
        capture is None
        or str(capture["workspace_id"]) != workspace_id
        or capture["state"] != "CAPTURED"
        or capture["cleanup_confirmed"] is not True
    ):
        raise builds.Refused("baseline has no completed capture")
    if (
        authorize
        and builds.read_binding(conn, workspace_id=workspace_id, run_id=str(capture["run_id"]))
        != capture["binding"]
    ):
        raise builds.Refused("original baseline capture authority changed")
    return dict(capture)


def _available(
    conn: psycopg.Connection[Any], archive: dict[str, Any], identity: tuple[str, str]
) -> None:
    location = (
        conn.execute(
            "SELECT store_endpoint,store_bucket FROM baseline_archive_restore_location "
            "WHERE build_id=%s ORDER BY revision DESC LIMIT 1",
            (archive["build_id"],),
        ).fetchone()
        or archive
    )
    if (location["store_endpoint"], location["store_bucket"]) != identity:
        raise builds.Refused("baseline archive storage location changed")
    if conn.execute(
        "SELECT 1 FROM baseline_archive_retirement WHERE build_id=%s", (archive["build_id"],)
    ).fetchone():
        raise builds.Refused("baseline archive retirement has started")
    _, days = _policy(conn, str(archive["workspace_id"]))
    if _expired(conn, archive["created_at"], days):
        raise builds.Refused("baseline archive retention expired")


def _bytes(store: CandidateArchiveStore, archive: dict[str, Any]) -> bytes:
    size = int(archive["size_bytes"])
    key = archive_key(
        str(archive["workspace_id"]), str(archive["build_id"]), archive["content_digest"]
    )
    if not 0 < size <= MAX_ARCHIVE_BYTES or key != archive["object_key"]:
        raise builds.Refused("baseline archive size or namespace changed")
    payload = store.get_bounded(key=key, max_bytes=size)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != archive["content_digest"]:
        raise builds.Refused("baseline archive bytes changed or were truncated")
    return payload


def retain_baseline(
    database_url: str, *, workspace_id: str, result: SandboxBuild, store: CandidateArchiveStore
) -> None:
    """Record intent before upload, then recheck original authority after verified read-back.

    Lost output cannot trigger another build. A failed upload stays quarantined for retirement.
    No artifact is published to a browser or accepted as a functional assertion here.
    """
    payload = result.artifact.archive()
    content_digest = hashlib.sha256(payload).hexdigest()
    key = archive_key(workspace_id, result.task_id, content_digest)
    with workspace_connection(database_url, workspace_id) as conn:
        capture = _capture(conn, workspace_id, result.task_id, authorize=True)
        observed = dict(
            artifact_digest=content_digest,
            source_archive_digest=result.source_archive_digest,
            container_id=result.container_id,
            platform=result.platform,
            image_id=result.image_id,
            daemon_endpoint=result.daemon.endpoint,
            daemon_id=result.daemon.daemon_id,
        )
        if result.cleanup_confirmed is not True or any(
            capture[k] != v for k, v in observed.items()
        ):
            raise builds.Refused("baseline upload differs from original capture")
        inserted = conn.execute(
            "INSERT INTO baseline_archive(build_id,workspace_id,content_digest,size_bytes,"
            "object_key,store_endpoint,store_bucket,state) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'QUARANTINED') "
            "ON CONFLICT(build_id) DO NOTHING RETURNING *",
            (
                result.task_id,
                workspace_id,
                content_digest,
                len(payload),
                key,
                *store.storage_identity,
            ),
        ).fetchone()
        if inserted is None:
            raise builds.Refused("baseline upload intent is already consumed")
        archive = dict(inserted)
        _available(conn, archive, store.storage_identity)
    store.put_create_only(key=key, payload=payload, content_type="application/x-tar")
    _bytes(store, archive)
    with workspace_connection(database_url, workspace_id) as conn:
        _capture(conn, workspace_id, result.task_id, authorize=True)
        _available(conn, archive, store.storage_identity)
        if (
            conn.execute(
                "UPDATE baseline_archive SET state='RETAINED',retained_at=clock_timestamp() "
                "WHERE build_id=%s AND state='QUARANTINED'",
                (result.task_id,),
            ).rowcount
            != 1
        ):
            raise builds.Refused("baseline retention raced or was invalidated")


def read_retained_baseline(
    database_url: str, *, workspace_id: str, build_id: str, store: CandidateArchiveStore
) -> SourceSnapshot:
    """Verified bytes only. Execution callers must separately recheck their current authority."""
    with workspace_connection(database_url, workspace_id) as conn:
        capture = _capture(conn, workspace_id, build_id, authorize=False)
        archive = conn.execute(
            "SELECT * FROM baseline_archive WHERE build_id=%s AND state='RETAINED' FOR SHARE",
            (build_id,),
        ).fetchone()
        if archive is None or archive["content_digest"] != capture["artifact_digest"]:
            raise builds.Refused("baseline archive is not retained")
        _available(conn, archive, store.storage_identity)
        source = read_artifact(_bytes(store, archive))
        _available(conn, archive, store.storage_identity)
        return source


def retire_expired_baseline(
    database_url: str, *, workspace_id: str, build_id: str, store: CandidateRetirementStore
) -> bool:
    """Close reads first; permanently tombstone the active-store key, not backups or replicas."""
    with workspace_connection(database_url, workspace_id) as conn:
        _capture(conn, workspace_id, build_id, authorize=False)
        archive = conn.execute(
            "SELECT * FROM baseline_archive WHERE build_id=%s FOR UPDATE", (build_id,)
        ).fetchone()
        if archive is None:
            raise builds.Refused("baseline archive is unavailable")
        location = (
            conn.execute(
                "SELECT store_endpoint,store_bucket FROM baseline_archive_restore_location "
                "WHERE build_id=%s ORDER BY revision DESC LIMIT 1",
                (build_id,),
            ).fetchone()
            or archive
        )
        if (location["store_endpoint"], location["store_bucket"]) != store.storage_identity:
            raise builds.Refused("baseline retirement location changed")
        key = archive_key(workspace_id, build_id, archive["content_digest"])
        if key != archive["object_key"]:
            raise builds.Refused("baseline retirement namespace changed")
        intent = conn.execute(
            "SELECT * FROM baseline_archive_retirement WHERE build_id=%s", (build_id,)
        ).fetchone()
        if intent is None:
            revision, days = _policy(conn, workspace_id)
            if not _expired(conn, archive["created_at"], days):
                return False
            conn.execute(
                "INSERT INTO baseline_archive_retirement"
                "(build_id,workspace_id,policy_revision,retain_days) VALUES(%s,%s,%s,%s)",
                (build_id, workspace_id, revision, days),
            )
        elif intent["completed_at"] is not None:
            return True
    store.retire_create_only(key=key)
    with workspace_connection(database_url, workspace_id) as conn:
        _capture(conn, workspace_id, build_id, authorize=False)
        location = (
            conn.execute(
                "SELECT store_endpoint,store_bucket FROM baseline_archive_restore_location "
                "WHERE build_id=%s ORDER BY revision DESC LIMIT 1",
                (build_id,),
            ).fetchone()
            or archive
        )
        if (location["store_endpoint"], location["store_bucket"]) != store.storage_identity:
            raise builds.Refused("baseline retirement location changed during storage I/O")
        conn.execute(
            "UPDATE baseline_archive SET state='DELETED',deleted_at=clock_timestamp() "
            "WHERE build_id=%s AND state<>'DELETED'",
            (build_id,),
        )
        conn.execute(
            "UPDATE baseline_archive_retirement SET completed_at=clock_timestamp() "
            "WHERE build_id=%s AND completed_at IS NULL",
            (build_id,),
        )
    return True
