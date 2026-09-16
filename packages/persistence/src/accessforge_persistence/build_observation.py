"""Offline build-registration input collection; no build, network, DB or execution authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from .source_intake import SourceIntakeError, resolve_source


def observe_build(repo: Path, artifact: Path, *, revision: str = "HEAD") -> dict[str, Any]:
    """Measure supplied inputs, not a causal relationship between them or deployed identity.

    Operators must supply a stable dedicated checkout and its actual retained build artifact.
    The returned body deliberately sets identityObservable false: hashing a local artifact cannot
    establish which bytes a deployment serves. Only identity metadata is emitted.
    """
    repo = repo.resolve(strict=True)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise SourceIntakeError(
            "this command requires no-follow, nonblocking file reads on this host"
        )
    if artifact.is_symlink() or not stat.S_ISREG(artifact.stat().st_mode):
        raise SourceIntakeError("artifact must be a regular file, not a symlink or special file")
    artifact = artifact.resolve(strict=True)
    if artifact.is_relative_to(repo):
        raise SourceIntakeError("keep the artifact outside the observed source checkout")
    source = resolve_source(repo, revision=revision)
    before = artifact.stat()
    descriptor = os.open(artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise SourceIntakeError("artifact changed before observation")
        artifact_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = artifact.lstat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or resolve_source(repo, revision=revision) != source:
        raise SourceIntakeError("inputs changed during observation; retry on stable inputs")
    return {
        "commitSha": source.commit_sha,
        "treeDigest": source.tree_digest,
        "dirty": source.dirty,
        "dirtyPaths": list(source.dirty_paths),
        "requestedRevision": revision,
        "artifactDigest": artifact_digest,
        "identityObservable": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Dedicated stable source checkout")
    parser.add_argument(
        "--artifact", type=Path, required=True, help="Actual build file outside checkout"
    )
    parser.add_argument("--revision", default="HEAD", help="Must resolve to the checked-out commit")
    args = parser.parse_args(argv)
    try:
        result = observe_build(args.repo, args.artifact, revision=args.revision)
    except (OSError, SourceIntakeError) as exc:
        print(f"Build observation refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
