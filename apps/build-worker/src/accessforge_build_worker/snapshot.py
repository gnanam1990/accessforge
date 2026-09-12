"""Bounded immutable candidate bytes, without extracting or executing repository content.

The source broker supplies an archive and the persisted source-tree identity. The dispatcher must
reload authority immediately before actual sandbox dispatch; the pure checks here cannot establish
that a previously fetched approval remains unrevoked. Preparing bytes produces no BUILT or VERIFIED
state. No source file is imported, no checkout is modified, and no file is written to the host.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from accessforge_domain.authority import Approval
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import ChangeVerdict, inspect_patch
from accessforge_domain.states import ApprovalScope, PatchStatus
from accessforge_persistence.patches import (
    MAX_CHANGE_BYTES,
    MAX_PATCH_BYTES,
    PatchProposal,
    patch_digest,
)

MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 4096


class SnapshotRefused(ValueError):
    """Input cannot safely represent the exact approved candidate."""


def _path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    try:
        encoded = name.encode("utf-8")
    except UnicodeError as exc:
        raise SnapshotRefused("source path is not valid UTF-8") from exc
    if (
        not name
        or len(encoded) > 1024
        or path.is_absolute()
        or name != path.as_posix()
        or any(part == ".." or part.casefold() == ".git" for part in path.parts)
        or "\\" in name
        or ":" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or unicodedata.normalize("NFC", name) != name
        or name == "."
    ):
        raise SnapshotRefused("source path is non-canonical or outside the source namespace")
    return path


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    content: bytes
    mode: int = 0o644

    def __post_init__(self) -> None:
        _path(self.path)
        if not isinstance(self.content, bytes):
            raise SnapshotRefused("source content must be immutable bytes")
        if self.mode not in {0o644, 0o755}:
            raise SnapshotRefused("only regular non-privileged source-file modes are supported")
        if len(self.content) > MAX_FILE_BYTES:
            raise SnapshotRefused("source file exceeds the byte limit")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    files: tuple[SourceFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.files, tuple):
            raise SnapshotRefused("source file collection must be immutable")
        if not self.files or len(self.files) > MAX_MEMBERS:
            raise SnapshotRefused("source must have a bounded nonempty file set")
        if sum(len(file.content) for file in self.files) > MAX_SOURCE_BYTES:
            raise SnapshotRefused("source exceeds the total byte limit")
        paths = [file.path.casefold() for file in self.files]
        if len(set(paths)) != len(paths):
            raise SnapshotRefused("duplicate or case-colliding source paths")
        names = set(paths)
        namespace: dict[str, str] = {}
        for file in self.files:
            path = PurePosixPath(file.path)
            for part in (path, *path.parents):
                name = part.as_posix()
                if namespace.setdefault(name.casefold(), name) != name:
                    raise SnapshotRefused("source directory names collide across platforms")
        for name in paths:
            if any(parent.as_posix() in names for parent in PurePosixPath(name).parents):
                raise SnapshotRefused("a source path is both a file and a parent directory")

    @property
    def tree_digest(self) -> str:
        """Match module 05's content-tree digest; bind file modes separately in archive_digest."""
        return digest(
            {
                "entries": [
                    {
                        "path": file.path,
                        "kind": "file",
                        "sha256": hashlib.sha256(file.content).hexdigest(),
                        "size": len(file.content),
                    }
                    for file in sorted(self.files, key=lambda file: PurePosixPath(file.path))
                ]
            }
        )

    def archive(self) -> bytes:
        """Canonical regular-file-only tar; never include caller-supplied tar metadata."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for file in sorted(self.files, key=lambda file: PurePosixPath(file.path)):
                member = tarfile.TarInfo(file.path)
                member.size = len(file.content)
                member.mode = file.mode
                member.mtime = 0
                archive.addfile(member, io.BytesIO(file.content))
        payload = buffer.getvalue()
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise SnapshotRefused("canonical archive exceeds the byte limit")
        return payload

    @property
    def archive_digest(self) -> str:
        """Bind path, content, executable mode and all emitted archive bytes."""
        return hashlib.sha256(self.archive()).hexdigest()


def read_snapshot(payload: bytes, *, expected_tree_digest: str) -> SourceSnapshot:
    """Reject unsafe archives before extraction; compressed input is intentionally unsupported."""
    snapshot = read_artifact(payload)
    if snapshot.tree_digest != expected_tree_digest:
        raise SnapshotRefused("source bytes differ from the persisted source-tree identity")
    return snapshot


def read_artifact(payload: bytes) -> SourceSnapshot:
    """Collect untrusted output bytes and compute identity ourselves, never from build stdout."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SnapshotRefused("source archive exceeds the byte limit")
    files: list[SourceFile] = []
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for index, member in enumerate(archive, start=1):
                if index > MAX_MEMBERS:
                    raise SnapshotRefused("archive exceeds the member limit")
                name = member.name.rstrip("/") if member.isdir() else member.name
                _path(name)
                folded = name.casefold()
                if folded in seen:
                    raise SnapshotRefused("duplicate or case-colliding archive members")
                seen.add(folded)
                if member.isdir():
                    continue
                if not member.isfile() or member.issparse():
                    raise SnapshotRefused("links, sparse files and special archive members refused")
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise SnapshotRefused("archive file exceeds the byte limit")
                total += member.size
                if total > MAX_SOURCE_BYTES:
                    raise SnapshotRefused("archive exceeds the expanded byte limit")
                stream = archive.extractfile(member)
                if stream is None:
                    raise SnapshotRefused("archive file content is missing")
                with stream:
                    content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise SnapshotRefused("archive file content is truncated")
                files.append(SourceFile(name, content, member.mode))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise SnapshotRefused("source is not a readable uncompressed tar archive") from exc
    return SourceSnapshot(tuple(files))


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    base_tree_digest: str
    base_archive_digest: str
    patch_digest: str
    source: SourceSnapshot
    # No boolean named success, build status, verification status or trusted regression result.


def prepare_candidate(
    base: SourceSnapshot,
    *,
    patch: PatchProposal,
    approval: Approval,
    workspace_id: str,
    application_paths: tuple[str, ...],
    now: str,
) -> PreparedCandidate:
    """Apply the exact approved bytes once, in memory, with current policy rechecked.

    All arguments must come from trusted persisted records, not the patch author's request body.
    A dispatcher still needs an atomic DB claim and a fresh approval check at sandbox dispatch.
    This implementation refuses dependency/build-policy edits, which need a separately provisioned
    toolchain; it must not silently build them under the original offline configuration.
    """
    if patch.status is not PatchStatus.APPROVED or patch.approval_id != approval.approval_id:
        raise SnapshotRefused("candidate preparation requires the patch's recorded approval")
    if base.tree_digest != patch.base_source_digest:
        raise SnapshotRefused("the approved source base has changed")
    if patch_digest(patch.changes) != patch.patch_digest:
        raise SnapshotRefused("the approved patch bytes have changed")
    approval.check(
        now=now,
        scope=ApprovalScope.PATCH_APPLY,
        workspace_id=workspace_id,
        target_id=patch.patch_id,
        target_digest=patch.patch_digest,
        current_revision=patch.revision,
    )
    if not application_paths or not patch.changes:
        raise SnapshotRefused("candidate requires a configured repair surface and a nonempty patch")
    for prefix in application_paths:
        _path(prefix)
    inspection = inspect_patch(patch.changes, application_paths=application_paths)
    if any(ruling.verdict is not ChangeVerdict.ALLOWED for ruling in inspection.rulings):
        raise SnapshotRefused("patch is outside the routine approved repair/build policy")
    changed = [change.path.casefold() for change in patch.changes]
    if len(set(changed)) != len(changed):
        raise SnapshotRefused("a patch may change each canonical path only once")
    entries = {file.path: file for file in base.files}
    patch_bytes = 0
    for change in patch.changes:
        _path(change.path)
        old = entries.get(change.path)
        if change.content is None:
            if old is None:
                raise SnapshotRefused("patch deletes a file absent from its base")
            del entries[change.path]
            continue
        content = change.content.encode("utf-8")
        patch_bytes += len(content)
        if len(content) > MAX_CHANGE_BYTES or patch_bytes > MAX_PATCH_BYTES:
            raise SnapshotRefused("patch exceeds the persisted proposal byte limits")
        if change.mode not in {None, "100644", "100755"}:
            raise SnapshotRefused("patch uses an unsupported file mode")
        mode = int(change.mode[-3:], 8) if change.mode else (old.mode if old else 0o644)
        entries[change.path] = SourceFile(change.path, content, mode)
    candidate = SourceSnapshot(tuple(entries.values()))
    return PreparedCandidate(base.tree_digest, base.archive_digest, patch.patch_digest, candidate)
