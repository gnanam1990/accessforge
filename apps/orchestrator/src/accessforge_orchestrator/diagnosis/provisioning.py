"""Read a private host-owned source allowlist for one already-authenticated human request."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .source import FrozenSourceScope

MAX_PROVISIONING_BYTES = 32768


class ProvisioningRefused(ValueError):
    pass


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvisioningRefused("duplicate provisioning field")
        result[key] = value
    return result


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def load_source_scope(path: Path, *, workspace_id: str, request_id: str) -> FrozenSourceScope:
    """No writes, chmod, source discovery or authority issuance.

    Requires a canonical POSIX private file and parent owned by the current user. This cooperative
    host boundary is not protection against root or a malicious process using that same OS user.
    Actual source commit/tree/file hashes are independently rechecked by delivery before disclosure.
    """
    if (
        os.name != "posix"
        or not path.is_absolute()
        or path.parent.resolve(strict=True) != path.parent
    ):
        raise ProvisioningRefused("canonical absolute POSIX provisioning path required")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ProvisioningRefused("provisioning directory must be private and owned by this user")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_PROVISIONING_BYTES
        ):
            raise ProvisioningRefused("bounded private regular provisioning file required")
        payload = os.read(descriptor, MAX_PROVISIONING_BYTES + 1)
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or len(payload) != before.st_size
            or _identity(after) != _identity(path.stat(follow_symlinks=False))
        ):
            raise ProvisioningRefused("provisioning file changed during read")
    finally:
        os.close(descriptor)
    config = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(config, dict)
        or set(config)
        != {
            "schemaVersion",
            "workspaceId",
            "requestId",
            "sourceRoot",
            "sourceCommitSha",
            "allowedFileDigests",
        }
        or type(config["schemaVersion"]) is not int
        or config["schemaVersion"] != 1
        or config["workspaceId"] != workspace_id
        or config["requestId"] != request_id
    ):
        raise ProvisioningRefused("provisioning file does not match this workspace and request")
    if not isinstance(config["sourceRoot"], str):
        raise ProvisioningRefused("frozen source root required")
    root = Path(config["sourceRoot"])
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
        raise ProvisioningRefused("canonical existing frozen source root required")
    commit = config["sourceCommitSha"]
    files = config["allowedFileDigests"]
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(files, dict)
        or not 1 <= len(files) <= 20
    ):
        raise ProvisioningRefused("exact frozen commit and bounded file allowlist required")
    for name, checksum in files.items():
        relative = PurePosixPath(name)
        if (
            not name
            or len(name) > 500
            or "\\" in name
            or "\x00" in name
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or str(relative) != name
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise ProvisioningRefused("canonical relative paths with exact file digests required")
    return FrozenSourceScope(root=root, commit_sha=commit, allowed_file_digests=files)
