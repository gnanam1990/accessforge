"""Durable candidate archives, separate from transcript evidence and never served as executable UI.

An intent is committed before upload. Missing/failed uploads remain quarantined and cannot create a
BUILT receipt. Retention follows bounded read-back, hash verification and a fresh worker fence.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Protocol

from accessforge_persistence import candidate_builds as builds
from accessforge_persistence import workspace_connection

from .sandbox import SandboxBuild
from .snapshot import MAX_ARCHIVE_BYTES, SourceSnapshot, read_artifact


class CandidateArchiveStore(Protocol):
    def put(self, *, key: str, payload: bytes, content_type: str) -> str: ...
    def get_bounded(self, *, key: str, max_bytes: int) -> bytes: ...


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
        conn.execute(
            "INSERT INTO candidate_archive "
            "(build_id,workspace_id,content_digest,size_bytes,object_key,"
            "stdout_digest,stderr_digest,state) VALUES (%s,%s,%s,%s,%s,%s,%s,'QUARANTINED') "
            "ON CONFLICT (build_id) DO NOTHING",
            (claim.build_id, workspace_id, content_digest, len(payload), key, stdout, stderr),
        )
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
    # No source execution, Docker credentials or author-selected key enters the object store path.
    store.put(key=key, payload=payload, content_type="application/x-tar")
    _checked_bytes(store, key, len(payload), content_digest)
    with workspace_connection(database_url, workspace_id) as conn:
        builds._owned(conn, claim, "DISPATCHED", None)
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
        row = conn.execute(
            "SELECT a.* FROM candidate_archive a JOIN candidate_build_attempt b "
            "ON b.id = a.build_id AND b.workspace_id = a.workspace_id "
            "WHERE a.build_id = %s AND a.state = 'RETAINED' AND b.state = 'BUILT' "
            "AND b.artifact_digest = a.content_digest",
            (build_id,),
        ).fetchone()
        if row is None:
            raise builds.BuildClaimRefused("no retained candidate archive is visible")
        key = archive_key(workspace_id, build_id, str(row["content_digest"]))
        if key != row["object_key"]:
            raise builds.BuildClaimRefused("stored archive key does not match its namespace")
    return read_artifact(
        _checked_bytes(store, key, int(row["size_bytes"]), str(row["content_digest"]))
    )
