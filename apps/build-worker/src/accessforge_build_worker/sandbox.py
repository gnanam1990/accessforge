"""E0 Docker execution primitive. Not a public API or durable build dispatcher.

Only preprovisioned, digest-pinned trusted toolchains may be used. Source travels through bounded
stdin, output through bounded stdout; no host source, socket, secret or credential mount is
accepted.
The caller must own the E0 target and recheck persisted authority before invoking this primitive.
Hosted hostile-customer builds remain unsupported: a shared kernel is not a tenant boundary.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .process import CommandResult, CommandStopped, run_bounded
from .snapshot import MAX_ARCHIVE_BYTES, SourceSnapshot, read_artifact

OWNER_LABEL = "io.accessforge.candidate-build"
USER = "65532:65532"
SCRATCH = "rw,nosuid,nodev,noexec,size=67108864,uid=65532,gid=65532,mode=0700"
MEMORY = 256 * 1024 * 1024
PIDS = 64


class SandboxRefused(RuntimeError):
    """No built artifact can be reported."""


class CleanupUnconfirmed(SandboxRefused):
    """The exact task container requires reconciliation; never report success or blindly retry."""


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    image: str
    wall_seconds: float = 60
    log_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", self.image):
            raise SandboxRefused("a preprovisioned digest-pinned toolchain is required")
        if not 0 < self.wall_seconds <= 600 or not 1 <= self.log_bytes <= 4 * 1024 * 1024:
            raise SandboxRefused("sandbox time/log limits are outside the supported bounds")


@dataclass(frozen=True, slots=True)
class SandboxBuild:
    task_id: str
    image_id: str
    source_archive_digest: str
    artifact: SourceSnapshot
    stdout: bytes
    stderr: bytes
    cleanup_confirmed: bool
    # Build completion is not an assertion outcome or a protected-regression attestation.


class DockerSandbox:
    def __init__(self, policy: SandboxPolicy) -> None:
        executable = shutil.which("docker")
        if executable is None:
            raise SandboxRefused("Docker is unavailable; no host-execution fallback")
        self.executable = executable
        self.policy = policy

    def _command(
        self,
        *args: str,
        deadline: float,
        limit: int = 1024 * 1024,
        input_bytes: bytes | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CommandResult:
        return run_bounded(
            (self.executable, *args),
            deadline=deadline,
            output_limit=limit,
            input_bytes=input_bytes,
            cancelled=cancelled,
        )

    def _checked(
        self,
        *args: str,
        deadline: float,
        limit: int = 1024 * 1024,
        input_bytes: bytes | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CommandResult:
        result = self._command(
            *args,
            deadline=deadline,
            limit=limit,
            input_bytes=input_bytes,
            cancelled=cancelled,
        )
        if result.code:
            # Raw stderr may contain source/build data. Do not leak it through operational logs.
            raise SandboxRefused(f"Docker {args[0]} did not complete successfully")
        return result

    def _inspect(self, target: str, *, deadline: float) -> dict[str, Any]:
        result = self._checked("container", "inspect", target, deadline=deadline)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SandboxRefused("container inspection returned an unexpected shape")
        return dict(rows[0])

    def _cleanup(self, name: str, task_id: str) -> None:
        """Resolve only this generated name and its ownership label, then remove by immutable ID."""
        deadline = time.monotonic() + 15
        # Listing distinguishes an absent container from a failed inspect/daemon connection.
        result = self._checked(
            "container",
            "ls",
            "-a",
            "--no-trunc",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.ID}}",
            deadline=deadline,
        )
        ids = result.stdout.decode().split()
        if not ids:
            return
        if len(ids) != 1:
            raise CleanupUnconfirmed(f"ambiguous cleanup target for {name}")
        item = self._inspect(ids[0], deadline=deadline)
        if item.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != task_id:
            raise CleanupUnconfirmed(f"ownership mismatch for {name}")
        self._checked("container", "rm", "--force", "--volumes", ids[0], deadline=deadline)
        remaining = self._checked(
            "container",
            "ls",
            "-a",
            "--no-trunc",
            "--filter",
            f"id={ids[0]}",
            "--format",
            "{{.ID}}",
            deadline=deadline,
        )
        if remaining.stdout.strip():
            raise CleanupUnconfirmed(f"container removal not confirmed for {name}")

    def build(
        self,
        source: SourceSnapshot,
        *,
        command: tuple[str, ...],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> SandboxBuild:
        """Run one owned build, hash captured output, and confirm removal before returning.

        Output is an immutable capture, not a claim that the build was honest or that no background
        writer existed. Protected regressions must run against these captured bytes in a fresh
        boundary, not against the mutable producer workspace.
        """
        if not command or not command[0].startswith("/") or any("\0" in arg for arg in command):
            raise SandboxRefused("build command must name an absolute in-container executable")
        if sum(len(arg.encode()) for arg in command) > 32768:
            raise SandboxRefused("build command exceeds the argv limit")
        if cancelled():
            raise CommandStopped("cancelled")
        archive = source.archive()
        deadline = time.monotonic() + self.policy.wall_seconds
        info = json.loads(self._checked("info", "--format", "{{json .}}", deadline=deadline).stdout)
        if info.get("OSType") != "linux" or info.get("CgroupVersion") != "2":
            raise SandboxRefused("a Linux Docker daemon with cgroup v2 is required")
        if not any("name=seccomp" in option for option in info.get("SecurityOptions", [])):
            raise SandboxRefused("daemon seccomp enforcement is unavailable")
        image = json.loads(
            self._checked("image", "inspect", self.policy.image, deadline=deadline).stdout
        )[0]
        if image.get("Config", {}).get("Volumes"):
            raise SandboxRefused("toolchain image declares unbounded implicit volumes")
        image_id = str(image["Id"])
        task_id = str(uuid.uuid4())
        name = f"accessforge-build-{task_id}"
        creation_confirmed = False
        try:
            created = self._checked(
                "container",
                "create",
                "--pull=never",
                "--name",
                name,
                "--label",
                f"{OWNER_LABEL}={task_id}",
                "--user",
                USER,
                "--read-only",
                "--network",
                "none",
                "--ipc",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                str(PIDS),
                "--cpus",
                "1",
                "--memory",
                str(MEMORY),
                "--memory-swap",
                str(MEMORY),
                "--ulimit",
                "nofile=128:128",
                "--ulimit",
                "core=0:0",
                "--log-driver",
                "none",
                "--no-healthcheck",
                "--tmpfs",
                f"/work:{SCRATCH}",
                "--env",
                "HOME=/work",
                "--env",
                "TMPDIR=/work/tmp",
                "--workdir",
                "/work",
                "--entrypoint",
                "/bin/sleep",
                self.policy.image,
                "86400",
                deadline=deadline,
                cancelled=cancelled,
            )
            container = created.stdout.decode().strip()
            if not re.fullmatch(r"[a-f0-9]{64}", container):
                raise SandboxRefused("creation did not return an immutable container ID")
            creation_confirmed = True
            self._assert_configuration(
                self._inspect(container, deadline=deadline),
                image_id=image_id,
                task_id=task_id,
            )
            self._checked("container", "start", container, deadline=deadline, cancelled=cancelled)
            self._checked(
                "exec",
                container,
                "/bin/mkdir",
                "/work/src",
                "/work/out",
                "/work/tmp",
                deadline=deadline,
                cancelled=cancelled,
            )
            self._checked(
                "exec",
                "-i",
                container,
                "/bin/tar",
                "-xf",
                "-",
                "-C",
                "/work/src",
                deadline=deadline,
                input_bytes=archive,
                cancelled=cancelled,
            )
            result = self._command(
                "exec",
                "--workdir",
                "/work/src",
                container,
                *command,
                deadline=deadline,
                limit=self.policy.log_bytes,
                cancelled=cancelled,
            )
            if result.code:
                raise SandboxRefused(f"build process exited with code {result.code}")
            output = self._checked(
                "exec",
                container,
                "/bin/tar",
                "-cf",
                "-",
                "-C",
                "/work",
                "out",
                deadline=deadline,
                limit=MAX_ARCHIVE_BYTES,
                cancelled=cancelled,
            )
            artifact = read_artifact(output.stdout)
            if cancelled():
                raise CommandStopped("cancelled")
            built = SandboxBuild(
                task_id,
                image_id,
                source.archive_digest,
                artifact,
                result.stdout,
                result.stderr,
                True,
            )
        finally:
            try:
                self._cleanup(name, task_id)
            except Exception as exc:
                raise CleanupUnconfirmed(
                    f"cleanup unconfirmed for {name}; reconcile before retry"
                ) from exc
            if not creation_confirmed:
                # An interrupted CLI can leave a create request running in the daemon. Absence
                # in one listing does not prove that a late creation cannot still materialize.
                raise CleanupUnconfirmed(
                    f"creation outcome unknown for {name}; reconcile before retry"
                )
        return built

    @staticmethod
    def _assert_configuration(item: dict[str, Any], *, image_id: str, task_id: str) -> None:
        host = item["HostConfig"]
        config = item["Config"]
        expected = {
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "IpcMode": "none",
            "Privileged": False,
            "Memory": MEMORY,
            "MemorySwap": MEMORY,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": PIDS,
        }
        if any(host.get(key) != value for key, value in expected.items()):
            raise SandboxRefused("daemon did not apply the required isolation/resource policy")
        if (
            item.get("Image") != image_id
            or config.get("User") != USER
            or config.get("Labels", {}).get(OWNER_LABEL) != task_id
            or host.get("Binds")
            or host.get("Mounts")
            or item.get("Mounts")
            or host.get("PidMode")
            or host.get("Devices")
            or host.get("DeviceRequests")
            or set(host.get("CapDrop") or []) != {"ALL"}
            or host.get("CapAdd")
            or "no-new-privileges" not in (host.get("SecurityOpt") or [])
            or host.get("Tmpfs") != {"/work": SCRATCH}
            or host.get("LogConfig", {}).get("Type") != "none"
            or config.get("Healthcheck", {}).get("Test") != ["NONE"]
            or sorted(
                (limit["Name"], limit["Soft"], limit["Hard"]) for limit in host.get("Ulimits", [])
            )
            != [("core", 0, 0), ("nofile", 128, 128)]
        ):
            raise SandboxRefused("unexpected mount, identity, capability or logging configuration")
