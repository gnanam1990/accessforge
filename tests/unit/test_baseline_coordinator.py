"""Synthetic sandbox receipts check dispatch ordering, not Docker or reader acceptance."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from accessforge_build_worker import baseline_coordinator as coordinator
from accessforge_build_worker.baseline_source import BaselineBinding, PreparedBaselineSource
from accessforge_build_worker.sandbox import (
    CleanupUnconfirmed,
    DaemonBinding,
    DockerSandbox,
    SandboxBuild,
    SandboxCreation,
    SandboxPolicy,
)
from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot
from accessforge_persistence import baseline_builds as builds
from accessforge_persistence.baseline_builds import Claim, Refused


@pytest.mark.parametrize("fault", [None, "dispatch", "created", "capture", "cleanup", "build"])
def test_committed_intent_and_fresh_receipts_before_capture(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    events: list[str] = []
    source = SourceSnapshot((SourceFile("app.py", b"not executed"),))
    prepared = PreparedBaselineSource(
        BaselineBinding(
            "ws", "run", "project", "m" * 64, "snap", source.tree_digest, source.archive_digest
        ),
        source,
    )
    value = Claim("task", "run", "token", 1)
    daemon = DaemonBinding("unix:///fixture/docker.sock", "fixture")
    image_id = "sha256:" + "a" * 64
    process = SandboxCreation("task", "b" * 64, image_id, "linux/amd64", daemon)
    result = SandboxBuild(
        "task",
        image_id,
        source.archive_digest,
        source,
        b"",
        b"",
        True,
        daemon,
        process.container_id,
        process.platform,
    )

    @contextmanager
    def connection(*args: Any) -> Iterator[object]:
        events.append("open")
        yield object()
        events.append("commit")

    def claim(*args: Any, **kwargs: Any) -> Claim:
        events.append("claim")
        assert kwargs["source_archive_digest"] == source.archive_digest
        return value

    def stage(name: str) -> Any:
        def apply(*args: Any, **kwargs: Any) -> None:
            events.append(name)
            assert kwargs["value"] == value
            if fault == name:
                raise Refused(name)

        return apply

    def fail(*args: Any, **kwargs: Any) -> None:
        events.append("fail")
        assert kwargs["cleanup_confirmed"] is (fault != "cleanup")

    def build(*args: Any, **kwargs: Any) -> SandboxBuild:
        assert events == ["open", "claim", "commit", "open", "dispatch", "commit"]
        events.append("build")
        kwargs["on_created"](process)
        assert events[-3:] == ["open", "created", "commit"]
        if fault == "cleanup":
            raise CleanupUnconfirmed("synthetic cleanup uncertainty")
        if fault == "build":
            raise RuntimeError("synthetic build failure with confirmed cleanup")
        return result

    sandbox = SimpleNamespace(
        policy=SandboxPolicy(image_id),
        daemon=daemon,
        _checked=lambda *args, **kwargs: SimpleNamespace(stdout=image_id.encode()),
        build=build,
    )
    monkeypatch.setattr(coordinator, "workspace_connection", connection)
    monkeypatch.setattr(builds, "claim", claim)
    for name in ("dispatch", "created", "captured"):
        monkeypatch.setattr(builds, name, stage("capture" if name == "captured" else name))
    monkeypatch.setattr(builds, "fail", fail)
    if fault is None:
        assert (
            coordinator.execute_baseline_build(
                "unused",
                prepared=prepared,
                sandbox=cast(DockerSandbox, sandbox),
                command=("/build",),
            )
            == result
        )
        assert events[-3:] == ["open", "capture", "commit"]
        assert "fail" not in events
    else:
        with pytest.raises((Refused, CleanupUnconfirmed, RuntimeError)):
            coordinator.execute_baseline_build(
                "unused",
                prepared=prepared,
                sandbox=cast(DockerSandbox, sandbox),
                command=("/build",),
            )
        if fault == "dispatch":
            assert "build" not in events
        else:
            assert events[-3:] == ["open", "fail", "commit"]
