"""Fail-closed policy checks; only tests/sandbox provides actual containment proof."""

from __future__ import annotations

from typing import Any

import pytest

from accessforge_build_worker.process import CommandResult, CommandStopped
from accessforge_build_worker.sandbox import (
    MEMORY,
    OWNER_LABEL,
    PIDS,
    SCRATCH,
    USER,
    CleanupUnconfirmed,
    DockerSandbox,
    SandboxPolicy,
    SandboxRefused,
)
from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot

IMAGE = "node@sha256:" + "a" * 64


@pytest.mark.parametrize("image", ["node:22", "node:latest", "node@sha256:123", "../bad"])
def test_mutable_or_invalid_toolchain_reference_is_refused(image: str) -> None:
    with pytest.raises(SandboxRefused, match="digest-pinned"):
        SandboxPolicy(image=image)


@pytest.mark.parametrize("seconds", [0, -1, 601, float("inf"), float("nan")])
def test_unbounded_runtime_is_refused(seconds: float) -> None:
    with pytest.raises(SandboxRefused, match="limits"):
        SandboxPolicy(image=IMAGE, wall_seconds=seconds)


def _configuration() -> dict[str, Any]:
    return {
        "Image": "sha256:trusted",
        "Config": {
            "User": USER,
            "Labels": {OWNER_LABEL: "task"},
            "Healthcheck": {"Test": ["NONE"]},
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "IpcMode": "none",
            "Privileged": False,
            "Memory": MEMORY,
            "MemorySwap": MEMORY,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": PIDS,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/work": SCRATCH},
            "LogConfig": {"Type": "none"},
            "Ulimits": [
                {"Name": "core", "Soft": 0, "Hard": 0},
                {"Name": "nofile", "Soft": 128, "Hard": 128},
            ],
        },
    }


@pytest.mark.parametrize(
    ("key", "unsafe"),
    [
        ("ReadonlyRootfs", False),
        ("NetworkMode", "host"),
        ("IpcMode", "host"),
        ("Privileged", True),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("NanoCpus", 0),
        ("PidsLimit", -1),
        ("Binds", ["/:/host"]),
        ("Devices", ["/dev/sda"]),
        ("DeviceRequests", [{"Driver": "nvidia"}]),
        ("PidMode", "host"),
        ("CapDrop", []),
        ("CapAdd", ["SYS_ADMIN"]),
        ("SecurityOpt", []),
        ("Tmpfs", {}),
        ("LogConfig", {"Type": "json-file"}),
        ("Ulimits", []),
    ],
)
def test_daemon_policy_drift_is_refused(key: str, unsafe: Any) -> None:
    item = _configuration()
    DockerSandbox._assert_configuration(item, image_id="sha256:trusted", task_id="task")
    item["HostConfig"][key] = unsafe
    with pytest.raises(SandboxRefused):
        DockerSandbox._assert_configuration(item, image_id="sha256:trusted", task_id="task")


def test_interrupted_creation_is_unknown_even_when_cleanup_observes_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("accessforge_build_worker.sandbox.shutil.which", lambda _: "/fake/docker")
    sandbox = DockerSandbox(SandboxPolicy(image=IMAGE))
    cleaned: list[str] = []

    def checked(*args: str, **kwargs: Any) -> CommandResult:
        if args[0] == "info":
            return CommandResult(
                0, b'{"OSType":"linux","CgroupVersion":"2","SecurityOptions":["name=seccomp"]}', b""
            )
        if args[0] == "image":
            return CommandResult(0, b'[{"Id":"sha256:trusted","Config":{}}]', b"")
        raise CommandStopped("deadline exceeded during create")

    monkeypatch.setattr(sandbox, "_checked", checked)
    monkeypatch.setattr(sandbox, "_cleanup", lambda name, task: cleaned.append(name))
    with pytest.raises(CleanupUnconfirmed, match="creation outcome unknown"):
        sandbox.build(SourceSnapshot((SourceFile("a", b"a"),)), command=("/bin/true",))
    assert len(cleaned) == 1
