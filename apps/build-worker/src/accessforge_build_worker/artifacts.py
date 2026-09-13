"""Durable candidate archives, separate from transcript evidence and never served as executable UI.

An intent is committed before upload. Missing/failed uploads remain quarantined and cannot create a
BUILT receipt. Retention follows bounded read-back, hash verification and a fresh worker fence.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any, Protocol

import psycopg

from accessforge_persistence import candidate_builds as builds
from accessforge_persistence import retention, workspace_connection

from .sandbox import SandboxBuild
from .snapshot import MAX_ARCHIVE_BYTES, SourceSnapshot, read_artifact


class CandidateArchiveStore(Protocol):
    @property
    def storage_identity(self) -> tuple[str, str]: ...
    def put_create_only(self, *, key: str, payload: bytes, content_type: str) -> str: ...
    def get_bounded(self, *, key: str, max_bytes: int) -> bytes: ...


class CandidateRetirementStore(Protocol):
    @property
    def storage_identity(self) -> tuple[str, str]: ...
    def retire_create_only(self, *, key: str) -> None: ...


def _assert_location(
    conn: psycopg.Connection[dict[str, Any]],
    archive: dict[str, Any],
    identity: tuple[str, str],
) -> None:
    restored = conn.execute(
        "SELECT store_endpoint,store_bucket FROM candidate_archive_restore_location "
        "WHERE build_id = %s ORDER BY revision DESC LIMIT 1",
        (archive["build_id"],),
    ).fetchone()
    location = restored or archive
    if (location["store_endpoint"], location["store_bucket"]) != identity:
        raise builds.BuildClaimRefused(
            "candidate archive storage location is unbound or mismatched"
        )


def _policy(conn: psycopg.Connection[dict[str, Any]], workspace_id: str) -> tuple[int, int]:
    policy = retention.current_policy(conn, workspace_id=workspace_id)
    sources = [entry for entry in policy.entries if entry.evidence_class == "SOURCE_SNAPSHOT"]
    if len(sources) != 1:
        raise builds.BuildClaimRefused("source retention policy is missing or ambiguous")
    return policy.revision, sources[0].retain_days


def _expired(conn: psycopg.Connection[dict[str, Any]], created: datetime, days: int) -> bool:
    row = conn.execute("SELECT clock_timestamp() AS moment").fetchone()
    assert row is not None
    return bool((row["moment"] - created).total_seconds() >= days * 86400)


def _assert_fresh(
    conn: psycopg.Connection[dict[str, Any]], workspace_id: str, created: datetime
) -> None:
    _, days = _policy(conn, workspace_id)
    if _expired(conn, created, days):
        raise builds.BuildClaimRefused("candidate archive retention has expired")


def archive_key(workspace_id: str, build_id: str, content_digest: str) -> str:
    if (
        str(uuid.UUID(workspace_id)) != workspace_id
        or str(uuid.UUID(build_id)) != build_id
        or not re.fullmatch(r"[a-f0-9]{64}", content_digest)
    ):
        raise builds.BuildClaimRefused("invalid candidate archive identity")
    return f"workspaces/{workspace_id}/candidate-builds/{build_id}/archives/{content_digest}"


def _checked_bytes(store: CandidateArchiveStore, key: str, size: int, content_digest: str) -> bytes:
    if not 0 < size <= MAX_ARCHIVE_BYTES:
        raise builds.BuildClaimRefused("invalid retained archive size")
    payload = store.get_bounded(key=key, max_bytes=size)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != content_digest:
        raise builds.BuildClaimRefused("stored candidate archive was truncated or substituted")
    return payload


def retain_candidate(
    database_url: str,
    *,
    workspace_id: str,
    claim: builds.BuildClaim,
    result: SandboxBuild,
    store: CandidateArchiveStore,
) -> None:
    payload = result.artifact.archive()
    content_digest = hashlib.sha256(payload).hexdigest()
    key = archive_key(workspace_id, claim.build_id, content_digest)
    stdout = hashlib.sha256(result.stdout).hexdigest()
    stderr = hashlib.sha256(result.stderr).hexdigest()
    with workspace_connection(database_url, workspace_id) as conn:
        attempt = builds._owned(conn, claim, "DISPATCHED", None)
        if (
            result.task_id != claim.build_id
            or not result.cleanup_confirmed
            or attempt["candidate_archive_digest"] != result.source_archive_digest
            or attempt["daemon_endpoint"] != result.daemon.endpoint
            or attempt["daemon_id"] != result.daemon.daemon_id
        ):
            raise builds.BuildClaimRefused("artifact receipt does not match the attempt")
        process = conn.execute(
            "SELECT container_id,image_id,platform FROM candidate_process_receipt "
            "WHERE build_id = %s",
            (claim.build_id,),
        ).fetchone()
        if process != {
            "container_id": result.container_id,
            "image_id": result.image_id,
            "platform": result.platform,
        }:
            raise builds.BuildClaimRefused("artifact has no matching durable creation receipt")
        inserted = conn.execute(
            "INSERT INTO candidate_archive "
            "(build_id,workspace_id,content_digest,size_bytes,object_key,"
            "stdout_digest,stderr_digest,state,storage_protocol,store_endpoint,store_bucket) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'QUARANTINED','CREATE_ONLY_V1',%s,%s) "
            "ON CONFLICT (build_id) DO NOTHING RETURNING build_id",
            (
                claim.build_id,
                workspace_id,
                content_digest,
                len(payload),
                key,
                stdout,
                stderr,
                *store.storage_identity,
            ),
        ).fetchone()
        if inserted is None:
            raise builds.BuildClaimRefused("candidate upload intent is already consumed")
        intent = conn.execute(
            "SELECT * FROM candidate_archive WHERE build_id = %s", (claim.build_id,)
        ).fetchone()
        if (
            intent is None
            or intent["state"] != "QUARANTINED"
            or intent["content_digest"] != content_digest
            or intent["size_bytes"] != len(payload)
            or intent["object_key"] != key
            or intent["stdout_digest"] != stdout
            or intent["stderr_digest"] != stderr
        ):
            raise builds.BuildClaimRefused("artifact intent already binds different bytes or state")
        _assert_fresh(conn, workspace_id, intent["created_at"])
        _assert_location(conn, intent, store.storage_identity)
    # No source execution, Docker credentials or author-selected key enters the object store path.
    store.put_create_only(key=key, payload=payload, content_type="application/x-tar")
    _checked_bytes(store, key, len(payload), content_digest)
    with workspace_connection(database_url, workspace_id) as conn:
        builds._owned(conn, claim, "DISPATCHED", None)
        _assert_location(conn, intent, store.storage_identity)
        if conn.execute(
            "SELECT 1 FROM candidate_archive_retirement WHERE build_id = %s", (claim.build_id,)
        ).fetchone():
            raise builds.BuildClaimRefused("candidate archive retirement has started")
        _assert_fresh(conn, workspace_id, intent["created_at"])
        changed = conn.execute(
            "UPDATE candidate_archive SET state = 'RETAINED', retained_at = clock_timestamp() "
            "WHERE build_id = %s AND content_digest = %s AND state = 'QUARANTINED'",
            (claim.build_id, content_digest),
        ).rowcount
        if changed != 1:
            raise builds.BuildClaimRefused("archive promotion raced or was invalidated")
        builds.finish_build(
            conn,
            claim=claim,
            artifact_digest=content_digest,
            candidate_archive_digest=result.source_archive_digest,
            cleanup_confirmed=True,
        )


def read_retained_candidate(
    database_url: str,
    *,
    workspace_id: str,
    build_id: str,
    store: CandidateArchiveStore,
) -> SourceSnapshot:
    """Reverify bounded bytes on every read; a database digest is not available content."""
    with workspace_connection(database_url, workspace_id) as conn:
        # Same lock order as promotion/retirement; an in-flight read precedes retirement intent.
        conn.execute("SELECT id FROM candidate_build_attempt WHERE id = %s FOR SHARE", (build_id,))
        row = conn.execute(
            "SELECT a.* FROM candidate_archive a JOIN candidate_build_attempt b "
            "ON b.id = a.build_id AND b.workspace_id = a.workspace_id "
            "WHERE a.build_id = %s AND a.state = 'RETAINED' AND b.state = 'BUILT' "
            "AND b.artifact_digest = a.content_digest "
            "AND NOT EXISTS (SELECT 1 FROM candidate_archive_retirement r "
            "WHERE r.build_id = a.build_id) FOR SHARE OF a",
            (build_id,),
        ).fetchone()
        if row is None:
            raise builds.BuildClaimRefused("no retained candidate archive is visible")
        _assert_location(conn, row, store.storage_identity)
        key = archive_key(workspace_id, build_id, str(row["content_digest"]))
        if key != row["object_key"]:
            raise builds.BuildClaimRefused("stored archive key does not match its namespace")
        _assert_fresh(conn, workspace_id, row["created_at"])
        source = read_artifact(
            _checked_bytes(store, key, int(row["size_bytes"]), str(row["content_digest"]))
        )
        _assert_fresh(conn, workspace_id, row["created_at"])
        return source


def retire_expired_candidate(
    database_url: str,
    *,
    workspace_id: str,
    build_id: str,
    store: CandidateRetirementStore,
) -> bool:
    """Retire active-store payload only. Permanent tombstones prohibit late create-only writes.

    A committed intent closes reads/promotion before storage I/O. Failure leaves that intent
    pending; calling again finishes the same retirement without executing a build.
    Backups and external copies are explicitly outside this single-store receipt.
    """
    with workspace_connection(database_url, workspace_id) as conn:
        attempt = conn.execute(
            "SELECT * FROM candidate_build_attempt WHERE id = %s FOR UPDATE", (build_id,)
        ).fetchone()
        archive = conn.execute(
            "SELECT * FROM candidate_archive WHERE build_id = %s FOR UPDATE", (build_id,)
        ).fetchone()
        if attempt is None or archive is None:
            raise builds.BuildClaimRefused("no candidate archive is visible")
        _assert_location(conn, archive, store.storage_identity)
        key = archive_key(workspace_id, build_id, str(archive["content_digest"]))
        if key != archive["object_key"] or archive["storage_protocol"] != "CREATE_ONLY_V1":
            raise builds.BuildClaimRefused(
                "legacy or mismatched archive needs operator reconciliation"
            )
        intent = conn.execute(
            "SELECT * FROM candidate_archive_retirement WHERE build_id = %s", (build_id,)
        ).fetchone()
        if intent is None:
            revision, days = _policy(conn, workspace_id)
            if not _expired(conn, archive["created_at"], days):
                return False
            conn.execute(
                "INSERT INTO candidate_archive_retirement "
                "(build_id,workspace_id,policy_revision,retain_days) VALUES (%s,%s,%s,%s)",
                (build_id, workspace_id, revision, days),
            )
            conn.execute(
                "UPDATE candidate_build_attempt SET state = 'UNKNOWN', epoch = epoch + 1, "
                "finished_at = clock_timestamp(), failure_code = 'ARCHIVE_EXPIRED' "
                "WHERE id = %s AND state IN ('CLAIMED','DISPATCHED')",
                (build_id,),
            )
        elif intent["completed_at"] is not None:
            return True
    store.retire_create_only(key=key)
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SELECT id FROM candidate_build_attempt WHERE id = %s FOR UPDATE", (build_id,))
        _assert_location(conn, archive, store.storage_identity)
        conn.execute(
            "UPDATE candidate_archive SET state = 'DELETED', deleted_at = clock_timestamp() "
            "WHERE build_id = %s AND state <> 'DELETED'",
            (build_id,),
        )
        updated = conn.execute(
            "UPDATE candidate_archive_retirement SET completed_at = clock_timestamp() "
            "WHERE build_id = %s AND completed_at IS NULL",
            (build_id,),
        ).rowcount
        if updated != 1:
            # A concurrent worker may have completed the identical tombstone; do not rewrite it.
            row = conn.execute(
                "SELECT completed_at FROM candidate_archive_retirement WHERE build_id = %s",
                (build_id,),
            ).fetchone()
            if row is None or row["completed_at"] is None:
                raise builds.BuildClaimRefused("retirement intent disappeared before completion")
    return True
