"""Read-only original-source comparisons; no candidate application or build authorization."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import inspect_patch
from accessforge_persistence import candidate_builds as builds
from accessforge_persistence import patches, workspace_connection

from .snapshot import SnapshotRefused, SourceFile
from .source_broker import BoundCommitSource, read_persisted_source

MAX_COMPARISON_FILES = 20
MAX_COMPARISON_BYTES = 2 * 1024 * 1024
MAX_COMPARISON_LINES = 20000
MAX_FILE_LINES = 4000


def _lines(text: str) -> list[str]:
    # Git's line boundary is LF, not Python splitlines' Unicode separators or bare CR.
    pieces = text.split("\n")
    return [piece + "\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _text(content: bytes) -> str:
    if len(content) > patches.MAX_CHANGE_BYTES or b"\0" in content:
        raise SnapshotRefused("comparison requires bounded non-binary UTF-8 source")
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SnapshotRefused(
            "original source is not valid UTF-8; comparison unavailable"
        ) from None


def _side(file: SourceFile | None) -> dict[str, Any] | None:
    if file is None:
        return None
    return {
        "text": _text(file.content),
        "sha256": hashlib.sha256(file.content).hexdigest(),
        "byteLength": len(file.content),
        "mode": f"100{file.mode:o}",
    }


def _unified(path: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    # Quoted paths prevent a newline/tab in a filename from inventing another diff header.
    old_path, new_path = json.dumps("a/" + path), json.dumps("b/" + path)
    headers = [f"diff --git {old_path} {new_path}\n"]
    if before is None and after is not None:
        headers.append(f"new file mode {after['mode']}\n")
    elif after is None and before is not None:
        headers.append(f"deleted file mode {before['mode']}\n")
    elif before is not None and after is not None and before["mode"] != after["mode"]:
        headers.extend([f"old mode {before['mode']}\n", f"new mode {after['mode']}\n"])
    old_lines = [] if before is None else _lines(str(before["text"]))
    new_lines = [] if after is None else _lines(str(after["text"]))
    for line in difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="/dev/null" if before is None else old_path,
        tofile="/dev/null" if after is None else new_path,
        n=3,
    ):
        # difflib omits the marker and separator for a source line without its final newline.
        headers.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(headers)


def compare_source(
    source: BoundCommitSource,
    *,
    patch: patches.PatchProposal,
    application_paths: tuple[str, ...],
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Compare verified immutable original bytes with a persisted proposal, without applying it.

    The full source snapshot was independently reconstructed by the Git object broker. Only paths
    named by the proposal leave this function. File modes and content hashes remain explicit even
    for mode-only changes. Refused/oversized/binary source is never replaced by an empty old file.
    """
    if cancelled():
        raise SnapshotRefused("source comparison cancelled")
    if not re.fullmatch(r"[0-9a-f]{40}", source.commit_sha):
        raise SnapshotRefused("comparison requires a full original commit identity")
    if source.source.tree_digest != patch.base_source_digest:
        raise SnapshotRefused("original source differs from the proposal base")
    if patches.patch_digest(patch.changes) != patch.patch_digest:
        raise SnapshotRefused("proposed bytes differ from their recorded patch digest")
    if not application_paths or not 1 <= len(patch.changes) <= MAX_COMPARISON_FILES:
        raise SnapshotRefused("comparison requires a bounded configured repair surface")
    inspection = inspect_patch(patch.changes, application_paths=application_paths)
    if not inspection.acceptable:
        raise SnapshotRefused("protected or unsupported patch cannot be compared for approval")
    original = {file.path: file for file in source.source.files}
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = total_lines = 0
    for change in patch.changes:
        if cancelled():
            raise SnapshotRefused("source comparison cancelled")
        if change.path.casefold() in seen:
            raise SnapshotRefused("comparison contains a duplicate or case-colliding path")
        seen.add(change.path.casefold())
        old = original.get(change.path)
        if old is None and any(p.casefold() == change.path.casefold() for p in original):
            raise SnapshotRefused("proposed path collides with a differently cased original")
        if change.content is None and old is None:
            raise SnapshotRefused("deletion names a file absent from the original source")
        if change.binary or change.mode not in {None, "100644", "100755"}:
            raise SnapshotRefused("binary or non-regular file mode is not a source comparison")
        mode = int(change.mode[-3:], 8) if change.mode else old.mode if old else 0o644
        new = (
            None
            if change.content is None
            else SourceFile(change.path, change.content.encode("utf-8"), mode)
        )
        before, after = _side(old), _side(new)
        for side in (before, after):
            if side is None:
                continue
            lines = len(_lines(str(side["text"])))
            total_lines += lines
            total_bytes += int(side["byteLength"])
            if (
                lines > MAX_FILE_LINES
                or total_lines > MAX_COMPARISON_LINES
                or total_bytes > MAX_COMPARISON_BYTES
            ):
                raise SnapshotRefused(
                    "comparison exceeded its byte/line budget; no truncated diff returned"
                )
        files.append(
            {
                "path": change.path,
                "operation": "DELETE" if new is None else "ADD" if old is None else "MODIFY",
                "before": before,
                "after": after,
                "changed": old != new,
                "unifiedDiff": _unified(change.path, before, after),
            }
        )
    payload = {
        "schemaVersion": 1,
        "patchId": patch.patch_id,
        "patchRevision": patch.revision,
        "patchDigest": patch.patch_digest,
        "baseManifestDigest": patch.base_manifest_digest,
        "baseSourceDigest": patch.base_source_digest,
        "baseCommitSha": source.commit_sha,
        "baseArchiveDigest": source.archive_digest,
        "files": files,
        "meaning": "ORIGINAL_SOURCE_COMPARISON_NOT_APPLICATION_OR_VERIFICATION",
    }
    return {"comparisonDigest": digest(payload), "comparison": payload}


def prepare_comparison(
    database_url: str,
    *,
    workspace_id: str,
    patch_id: str,
    actor_id: str,
    repositories: Mapping[str, Path],
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Trusted operator entry; repository mappings never come from a public API request.

    All access is read-only. Membership, project authorization, proposal and original identity are
    read under workspace isolation; locked records cannot change while source is prepared. No Git
    checkout, filter, hook, fetch, application script, model or build command is invoked.
    """
    with workspace_connection(database_url, workspace_id) as conn:
        member = conn.execute(
            "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, actor_id),
        ).fetchone()
        if member is None or Permission.EVIDENCE_READ not in permissions_for(Role(member["role"])):
            raise SnapshotRefused("comparison requires current evidence-read permission")
        conn.execute("SELECT id FROM patch_proposal WHERE id=%s FOR SHARE", (patch_id,))
        patch = patches.load_patch(conn, patch_id=patch_id)
        baseline = builds._baseline(conn, patch_id, workspace_id)
        if digest(baseline["canonical_manifest"]) != patch.base_manifest_digest:
            raise SnapshotRefused("original manifest integrity unavailable")
        source = read_persisted_source(
            conn,
            workspace_id=workspace_id,
            source_snapshot_id=str(baseline["source_snapshot_id"]),
            repositories=repositories,
            cancelled=cancelled,
        )
        comparison = compare_source(
            source.bound,
            patch=patch,
            application_paths=tuple(baseline["paths"]),
            cancelled=cancelled,
        )
        payload = {
            **comparison["comparison"],
            "workspaceId": workspace_id,
            "projectId": source.project_id,
            "sourceSnapshotId": source.source_snapshot_id,
            "requestedBy": actor_id,
        }
        return {"comparisonDigest": digest(payload), "comparison": payload}
