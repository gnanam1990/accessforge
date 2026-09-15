"""Explicit trusted-host publication command. No key discovery or work at import time."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from uuid import UUID

from accessforge_domain.authorization import HumanPrincipal, Role
from accessforge_persistence import github_previews, workspace_connection

from .github_app_jwt import sign_app_jwt
from .github_connections import _authorize
from .github_publisher import publish_preview


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


def _private_bytes(path: Path, maximum: int) -> bytes:
    """Read only an explicitly supplied bounded private file; never chmod or discover keys.

    Requires an isolated trusted OS account. Filesystem checks do not isolate root or another
    process sharing that account, and Python does not guarantee secret-memory zeroization.
    """
    if os.name != "posix" or not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("canonical POSIX file required")
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("private owned parent required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("private bounded regular file required")
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or _identity(before) != _identity(after)
            or _identity(after) != _identity(path.stat(follow_symlinks=False))
        ):
            raise ValueError("file changed during read")
        return raw
    finally:
        os.close(fd)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration field")
        result[key] = value
    return result


def _scope(path: Path) -> dict[str, Any]:
    value = json.loads(_private_bytes(path, 8192).decode("utf-8"), object_pairs_hook=_unique)
    identifiers = {"workspaceId", "userId", "sessionId", "previewId", "approvalId"}
    if (
        not isinstance(value, dict)
        or set(value) != identifiers | {"schemaVersion", "appId", "previewDigest"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or type(value["appId"]) is not int
        or not 1 <= value["appId"] <= 2**63 - 1
        or not isinstance(value["previewDigest"], str)
        or re.fullmatch(r"[a-f0-9]{64}", value["previewDigest"]) is None
    ):
        raise ValueError("publication scope unavailable")
    for key in identifiers:
        if not isinstance(value[key], str) or str(UUID(value[key])) != value[key]:
            raise ValueError("canonical identity required")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Publish one original approved GitHub preview")
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--allow-github-publication", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_github_publication:
        parser.error("--allow-github-publication required; no file read or remote work started")
    entered = False
    try:
        scope = _scope(args.scope_file)
        principal = HumanPrincipal(
            scope["userId"], scope["workspaceId"], Role.OWNER, scope["sessionId"]
        )
        database_url = os.environ["ACCESSFORGE_DATABASE_URL"]
        # Private operator input is not proof of current application authority. Check it before
        # loading signing material; publisher independently rechecks before remote dispatch.
        with workspace_connection(database_url, principal.workspace_id) as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            _authorize(conn, principal)
            stored = github_previews.read(conn, preview_id=scope["previewId"])
            if (
                stored["preview_digest"] != scope["previewDigest"]
                or int(stored["preview"]["identity"]["appId"]) != scope["appId"]
            ):
                raise ValueError("configured App or approved preview differs")
        from .maintenance.purge_worker import _store_from_environment

        store = _store_from_environment()
        jwt = sign_app_jwt(app_id=scope["appId"], private_pem=_private_bytes(args.key_file, 16384))
        entered = True
        receipt = publish_preview(
            database_url,
            principal=principal,
            preview_id=scope["previewId"],
            approval_id=scope["approvalId"],
            expected_digest=scope["previewDigest"],
            app_jwt=jwt.bearer,
            store=store,
            allow_temporary_token_issuance=True,
        )
    except (Exception, KeyboardInterrupt):
        # Static output only: never include paths, raw SDK errors, key/JWT/session or response data.
        if not entered:
            parser.exit(2, "GITHUB_PUBLICATION_REFUSED; publisher was not invoked.\n")
        parser.exit(
            1,
            "GITHUB_PUBLICATION_UNCONFIRMED; inspect original preview recovery; "
            "never automatically retry.\n",
        )
    print(f"GITHUB_CREATION_RETAINED intent={receipt.intent_id} check={receipt.check_run_id}")


if __name__ == "__main__":
    main()
