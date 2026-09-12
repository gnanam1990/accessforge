"""Narrow read-only access to an exact frozen source tree."""

from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .models import SourceExcerpt


class SourceReadRefused(RuntimeError):
    """A source request was outside the sealed revision, path or byte envelope."""


RevisionProbe = Callable[[Path], str]
_SECRET_NAMES = frozenset({".env", ".npmrc", ".pypirc", "credentials", "secrets"})
_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})


def _git_head(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise SourceReadRefused("cannot establish the frozen source revision: git is unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - fixed read-only argv, no model-controlled command
            [git, "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceReadRefused("cannot establish the frozen source revision") from exc
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class FrozenSourceScope:
    root: Path
    commit_sha: str
    allowed_file_digests: Mapping[str, str]
    max_files: int = 20
    max_total_bytes: int = 250_000
    max_excerpt_lines: int = 200

    def __post_init__(self) -> None:
        if not self.root.is_dir():
            raise SourceReadRefused("frozen source root is not a directory")
        if self.max_files < 1 or self.max_files > 100:
            raise ValueError("source file budget must be between 1 and 100")
        if self.max_total_bytes < 1 or self.max_total_bytes > 5_000_000:
            raise ValueError("source byte budget must be between 1 and 5000000")
        if self.max_excerpt_lines < 1 or self.max_excerpt_lines > 1000:
            raise ValueError("source excerpt budget must be between 1 and 1000 lines")


@dataclass(slots=True)
class FrozenSourceReader:
    scope: FrozenSourceScope
    revision_probe: RevisionProbe = _git_head
    _read_files: set[str] = field(default_factory=set, init=False)
    _bytes_read: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        observed = self.revision_probe(self.scope.root)
        if observed != self.scope.commit_sha:
            raise SourceReadRefused(
                f"wrong frozen revision: observed {observed!r}, expected {self.scope.commit_sha!r}"
            )

    def read(self, path: str, *, line_start: int, line_end: int) -> SourceExcerpt:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise SourceReadRefused("path traversal or absolute source path refused")
        if (
            any(part in _SECRET_NAMES for part in relative.parts)
            or relative.suffix in _SECRET_SUFFIXES
        ):
            raise SourceReadRefused("secret-like file is never available to diagnosis")
        normalized = relative.as_posix()
        expected_digest = self.scope.allowed_file_digests.get(normalized)
        if expected_digest is None:
            raise SourceReadRefused("path is not in the sealed source scope")
        if line_start < 1 or line_end < line_start:
            raise SourceReadRefused("source line range is invalid")
        if line_end - line_start + 1 > self.scope.max_excerpt_lines:
            raise SourceReadRefused("source excerpt line budget exhausted")

        candidate = self.scope.root.joinpath(*relative.parts)
        current = self.scope.root
        for part in relative.parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except OSError as exc:
                raise SourceReadRefused("source path is missing from the frozen tree") from exc
            if stat.S_ISLNK(mode):
                raise SourceReadRefused("symlink source path refused")
        resolved_root = self.scope.root.resolve()
        resolved = candidate.resolve()
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            raise SourceReadRefused("source path escaped the frozen root")

        raw = resolved.read_bytes()
        observed_digest = hashlib.sha256(raw).hexdigest()
        if observed_digest != expected_digest:
            raise SourceReadRefused("source file digest no longer matches the frozen tree")
        new_file = normalized not in self._read_files
        next_file_count = len(self._read_files) + int(new_file)
        next_bytes = self._bytes_read + len(raw)
        if next_file_count > self.scope.max_files:
            raise SourceReadRefused("source file budget exhausted")
        if next_bytes > self.scope.max_total_bytes:
            raise SourceReadRefused("source byte budget exhausted")

        try:
            lines = raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise SourceReadRefused("source file is not bounded UTF-8 text") from exc
        if line_end > len(lines):
            raise SourceReadRefused("source line range exceeds the frozen file")
        self._read_files.add(normalized)
        self._bytes_read = next_bytes
        return SourceExcerpt(
            path=normalized,
            file_digest=observed_digest,
            line_start=line_start,
            line_end=line_end,
            text="\n".join(lines[line_start - 1 : line_end]),
        )
