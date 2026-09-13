"""Recover exact committed source from an operator-selected local Git object database.

Never checkout, archive, apply attributes/filters, run hooks or fetch. Input identity must be loaded
from the trusted source_snapshot record; neither repository path nor identity is author authority.
Dirty snapshots require a separately retained trusted artifact and are refused by this commit path.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from accessforge_persistence.source_intake import SourceIdentity

from .process import run_bounded
from .snapshot import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    MAX_MEMBERS,
    MAX_SOURCE_BYTES,
    SnapshotRefused,
    SourceFile,
    SourceSnapshot,
    _path,
)

OID = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class BoundCommitSource:
    commit_sha: str
    source: SourceSnapshot

    @property
    def archive_digest(self) -> str:
        return self.source.archive_digest


@dataclass(frozen=True, slots=True)
class PersistedCommitSource:
    workspace_id: str
    project_id: str
    source_snapshot_id: str
    bound: BoundCommitSource


def read_persisted_source(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    source_snapshot_id: str,
    repositories: Mapping[str, Path],
    cancelled: Callable[[], bool] = lambda: False,
) -> PersistedCommitSource:
    """Select identity under RLS and resolve the repository only from operator configuration.

    `repositories` maps project IDs to provisioned local object databases. It must not come from a
    request. This read is source preparation, not a durable claim or permission to execute a patch.
    """
    row = conn.execute(
        """
        SELECT s.id, s.project_id, s.commit_sha, s.tree_digest, s.dirty, s.dirty_path_count
          FROM source_snapshot s
          JOIN project p ON p.id = s.project_id AND p.workspace_id = s.workspace_id
         WHERE s.id = %s AND s.workspace_id = %s
           AND p.revoked_at IS NULL AND p.repository_authorized_by IS NOT NULL
        """,
        (source_snapshot_id, workspace_id),
    ).fetchone()
    if row is None:
        raise SnapshotRefused("source snapshot is unavailable or repository authority was revoked")
    project_id = str(row["project_id"])
    repository = repositories.get(project_id)
    if repository is None:
        raise SnapshotRefused("project has no operator-provisioned source repository")
    if row["dirty"] or row["dirty_path_count"]:
        raise SnapshotRefused("dirty source needs a retained trusted artifact, not commit recovery")
    bound = read_committed_source(
        repository,
        identity=SourceIdentity(str(row["commit_sha"]), str(row["tree_digest"]), False),
        cancelled=cancelled,
    )
    return PersistedCommitSource(workspace_id, project_id, str(row["id"]), bound)


def _decode_batch(payload: bytes, requests: tuple[tuple[str, str], ...]) -> list[bytes]:
    """Check framing, requested types and object hashes, not only Git's printed object IDs."""
    result: list[bytes] = []
    offset = 0
    for oid, kind in requests:
        end = payload.find(b"\n", offset, offset + 128)
        if end < 0:
            raise SnapshotRefused("missing or malformed Git object header")
        fields = payload[offset:end].split(b" ")
        if len(fields) != 3 or fields[:2] != [oid.encode(), kind.encode()]:
            raise SnapshotRefused("Git object is missing or has a different identity/type")
        size_text = fields[2]
        if not size_text.isdigit() or len(size_text) > 9:
            raise SnapshotRefused("invalid Git object size")
        size = int(size_text)
        limit = MAX_FILE_BYTES if kind == "blob" else 1024 * 1024
        if size > limit:
            raise SnapshotRefused("Git object exceeds its byte limit")
        start = end + 1
        content = payload[start : start + size]
        offset = start + size + 1
        if len(content) != size or payload[offset - 1 : offset] != b"\n":
            raise SnapshotRefused("Git object content is truncated")
        # SHA-1 is the existing Git object-address format, not the archive/provenance digest.
        address = hashlib.sha1(
            kind.encode() + b" " + str(size).encode() + b"\0" + content,
            usedforsecurity=False,
        ).hexdigest()
        if address != oid:
            raise SnapshotRefused("Git object bytes do not match their content address")
        result.append(content)
    if offset != len(payload):
        raise SnapshotRefused("unexpected trailing Git object response")
    return result


def read_committed_source(
    repository: Path,
    *,
    identity: SourceIdentity,
    wall_seconds: float = 60,
    cancelled: Callable[[], bool] = lambda: False,
) -> BoundCommitSource:
    """Bind file modes and bytes to the persisted commit, independent of mutable checkout state.

    Repository is trusted operator configuration (not a request path). The commit must already be
    local. A missing object fails closed even when the repository declares a promisor remote.
    """
    if identity.dirty or identity.dirty_paths:
        raise SnapshotRefused("dirty source needs a retained trusted artifact, not commit recovery")
    if not OID.fullmatch(identity.commit_sha):
        raise SnapshotRefused("a full immutable SHA-1 commit identity is required")
    if not 0 < wall_seconds <= 600:
        raise SnapshotRefused("source broker deadline is outside supported bounds")
    git = shutil.which("git")
    if git is None:
        raise SnapshotRefused("trusted Git executable unavailable")
    deadline = time.monotonic() + wall_seconds
    consumed = 0

    def objects(requests: tuple[tuple[str, str], ...]) -> list[bytes]:
        nonlocal consumed
        remaining = MAX_ARCHIVE_BYTES - consumed
        if remaining <= 0:
            raise SnapshotRefused("Git source object traversal exceeds the byte budget")
        response = run_bounded(
            (
                git,
                "--no-pager",
                "--no-replace-objects",
                "--no-lazy-fetch",
                "-c",
                "protocol.allow=never",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(repository.resolve(strict=True)),
                "cat-file",
                "--batch",
            ),
            input_bytes=b"".join(oid.encode() + b"\n" for oid, _ in requests),
            deadline=deadline,
            output_limit=remaining,
            cancelled=cancelled,
            # Do not forward host credentials, GIT_DIR, alternate object directories or tracing.
            env={
                "PATH": os.defpath,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_LAZY_FETCH": "1",
            },
        )
        consumed += len(response.stdout) + len(response.stderr)
        if response.code:
            raise SnapshotRefused("local Git object read failed; no checkout/fetch fallback")
        return _decode_batch(response.stdout, requests)

    commit = objects(((identity.commit_sha, "commit"),))[0]
    tree_line = commit.split(b"\n", 1)[0]
    if not re.fullmatch(rb"tree [0-9a-f]{40}", tree_line):
        raise SnapshotRefused("commit lacks its canonical tree identity")
    pending = [("", tree_line[5:].decode())]
    blobs: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    members = 0
    while pending:
        trees = objects(tuple((oid, "tree") for _, oid in pending))
        next_trees: list[tuple[str, str]] = []
        for (prefix, _), tree in zip(pending, trees, strict=True):
            offset = 0
            while offset < len(tree):
                end = tree.find(b"\0", offset)
                if end < 0 or end + 21 > len(tree):
                    raise SnapshotRefused("truncated Git tree entry")
                entry = tree[offset:end].split(b" ", 1)
                if len(entry) != 2:
                    raise SnapshotRefused("malformed Git tree entry")
                mode, raw_name = entry
                if b"/" in raw_name:
                    raise SnapshotRefused("Git tree entry contains a path separator")
                try:
                    path = prefix + raw_name.decode("utf-8")
                except UnicodeError as exc:
                    raise SnapshotRefused("Git path is not UTF-8") from exc
                _path(path)
                if path.casefold() in seen:
                    raise SnapshotRefused("duplicate or case-colliding Git tree entry")
                seen.add(path.casefold())
                oid = tree[end + 1 : end + 21].hex()
                offset = end + 21
                members += 1
                if members > MAX_MEMBERS:
                    raise SnapshotRefused("Git source exceeds the member limit")
                if mode == b"40000":
                    next_trees.append((path + "/", oid))
                elif mode in {b"100644", b"100755"}:
                    blobs.append((path, oid, int(mode, 8) & 0o777))
                else:
                    raise SnapshotRefused(
                        "Git links, submodules and unsupported file modes refused"
                    )
        pending = next_trees
    if not blobs:
        raise SnapshotRefused("committed source contains no regular files")
    contents = objects(tuple((oid, "blob") for _, oid, _ in blobs))
    if sum(map(len, contents)) > MAX_SOURCE_BYTES:
        raise SnapshotRefused("committed source exceeds the total byte limit")
    source = SourceSnapshot(
        tuple(
            SourceFile(path, content, mode)
            for (path, _, mode), content in zip(blobs, contents, strict=True)
        )
    )
    if source.tree_digest != identity.tree_digest:
        raise SnapshotRefused("commit content differs from the persisted source-tree identity")
    return BoundCommitSource(identity.commit_sha, source)
