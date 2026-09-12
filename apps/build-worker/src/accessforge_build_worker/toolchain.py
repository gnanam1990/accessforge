"""Provision only the checked-in owned-reference toolchain, never a target repository context."""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .sandbox import DaemonBinding, SandboxRefused, _docker_command
from .snapshot import SourceFile, SourceSnapshot

REFERENCE_BUILD_COMMAND = (
    "/usr/local/bin/python",
    "-I",
    "/opt/accessforge/build_reference.py",
)
_CONTEXT_FILES = (
    "Dockerfile",
    "build-requirements.lock",
    "runtime-requirements.lock",
    "build_reference.py",
)


@dataclass(frozen=True, slots=True)
class ToolchainReceipt:
    image_id: str
    context_digest: str
    daemon: DaemonBinding


def provision_reference_toolchain(directory: Path, *, daemon: DaemonBinding) -> ToolchainReceipt:
    """Trusted maintainer provisioning with PyPI access, separate from no-network source dispatch.

    This directory is operator-controlled checked-in code, never a tenant-selected path. Only
    the enumerated frontend/locks/Dockerfile are sent; the target application is not in the context.
    """
    source = SourceSnapshot(
        tuple(SourceFile(name, (directory / name).read_bytes()) for name in _CONTEXT_FILES)
    )
    dockerfile = source.files[0].content.decode()
    if not re.match(r"FROM python@sha256:[a-f0-9]{64}\n", dockerfile):
        raise SandboxRefused("reference toolchain base must be pinned to its registry digest")
    executable = shutil.which("docker")
    if executable is None:
        raise SandboxRefused("Docker is unavailable")
    deadline = time.monotonic() + 600

    def checked(args: tuple[str, ...], *, payload: bytes = b"") -> bytes:
        result = _docker_command(
            executable,
            daemon.endpoint,
            args,
            deadline=deadline,
            limit=1024 * 1024,
            input_bytes=payload,
        )
        if result.code:
            raise SandboxRefused(
                "owned toolchain provisioning failed: "
                + result.stderr.decode(errors="replace")[-1500:]
            )
        return result.stdout

    if checked(("info", "--format", "{{.ID}}")).decode().strip() != daemon.daemon_id:
        raise SandboxRefused("provisioning daemon changed")
    output = checked(
        (
            "build",
            "--quiet",
            "--pull=false",
            "--label",
            f"io.accessforge.toolchain-context={source.archive_digest}",
            "-",
        ),
        payload=source.archive(),
    )
    image_id = output.decode().strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise SandboxRefused("provisioning did not return an immutable local image ID")
    if checked(("info", "--format", "{{.ID}}")).decode().strip() != daemon.daemon_id:
        raise SandboxRefused("provisioning daemon changed")
    return ToolchainReceipt(image_id, source.archive_digest, daemon)
