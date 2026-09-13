"""Fail-closed policy checks; only tests/sandbox provides actual containment proof."""

from __future__ import annotations

import time
from pathlib import Path
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
    DaemonBinding,
    DockerSandbox,
    SandboxPolicy,
    SandboxRefused,
    _docker_command,
)
from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot

IMAGE = "node@sha256:" + "a" * 64


@pytest.mark.parametrize(
    "image", ["node:22", "node:latest", "node@sha256:123", "../bad", "sha256:123"]
)
def test_mutable_or_invalid_toolchain_reference_is_refused(image: str) -> None:
    with pytest.raises(SandboxRefused, match="digest-pinned"):
        SandboxPolicy(image=image)


def test_full_immutable_local_image_id_is_accepted() -> None:
    image = "sha256:" + "a" * 64
    assert SandboxPolicy(image=image).image == image


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
    sandbox = DockerSandbox(
        SandboxPolicy(image=IMAGE), daemon=DaemonBinding("unix:///test/docker.sock", "test-daemon")
    )
    cleaned: list[str] = []

    def checked(*args: str, **kwargs: Any) -> CommandResult:
        if args[0] == "info":
            return CommandResult(
                0,
                b'{"ID":"test-daemon","OSType":"linux","CgroupVersion":"2",'
                b'"SecurityOptions":["name=seccomp"]}',
                b"",
            )
        if args[0] == "image":
            return CommandResult(0, b'[{"Id":"sha256:trusted","Config":{}}]', b"")
        raise CommandStopped("deadline exceeded during create")

    monkeypatch.setattr(sandbox, "_checked", checked)
    monkeypatch.setattr(sandbox, "_cleanup", lambda name, task: cleaned.append(name))
    with pytest.raises(CleanupUnconfirmed, match="creation outcome unknown"):
        sandbox.build(SourceSnapshot((SourceFile("a", b"a"),)), command=("/bin/true",))
    assert len(cleaned) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "tcp://127.0.0.1:2375",
        "ssh://host",
        "unix://relative",
        "unix:///tmp/../docker.sock",
        "unix:///tmp/docker.sock?query",
        "unix:///tmp/docker.sock\n",
    ],
)
def test_only_explicit_canonical_local_endpoints_are_accepted(endpoint: str) -> None:
    with pytest.raises(SandboxRefused, match="local Unix"):
        DaemonBinding(endpoint, "test-daemon")


def test_cli_uses_empty_config_and_does_not_forward_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_CONTEXT", "wrong-host")
    monkeypatch.setenv("DOCKER_HOST", "tcp://forbidden")
    monkeypatch.setenv("HTTP_PROXY", "synthetic-secret-proxy")
    configs: list[Path] = []

    def bounded(argv: tuple[str, ...], **kwargs: Any) -> CommandResult:
        assert argv[1] == "--config"
        configs.append(Path(argv[2]))
        assert configs[-1].is_dir() and not list(configs[-1].iterdir())
        assert argv[3:] == ("--host", "unix:///test/docker.sock", "info")
        assert set(kwargs["env"]) == {"PATH"}
        return CommandResult(0, b"ok", b"")

    monkeypatch.setattr("accessforge_build_worker.sandbox.run_bounded", bounded)
    _docker_command(
        "/fake/docker", "unix:///test/docker.sock", ("info",), deadline=time.monotonic() + 1
    )
    assert len(configs) == 1 and not configs[0].exists()


@pytest.mark.parametrize("change_after_absence", [False, True])
def test_daemon_identity_drift_cannot_confirm_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    change_after_absence: bool,
) -> None:
    monkeypatch.setattr("accessforge_build_worker.sandbox.shutil.which", lambda _: "/fake/docker")
    sandbox = DockerSandbox(
        SandboxPolicy(image=IMAGE), daemon=DaemonBinding("unix:///test/docker.sock", "original")
    )
    info_calls = 0

    def checked(*args: str, **kwargs: Any) -> CommandResult:
        nonlocal info_calls
        if args[0] == "info":
            info_calls += 1
            identity = b"original" if change_after_absence and info_calls == 1 else b"replacement"
            return CommandResult(0, identity, b"")
        assert args[:2] == ("container", "ls"), "must never remove on a replacement daemon"
        return CommandResult(0, b"", b"")

    monkeypatch.setattr(sandbox, "_checked", checked)
    with pytest.raises(CleanupUnconfirmed, match="daemon identity changed"):
        sandbox._cleanup("accessforge-build-owned", "owned")
