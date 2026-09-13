"""Provisioning boundary tests; actual wheel execution is an integration test, not mocked here."""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from accessforge_build_worker import toolchain
from accessforge_build_worker.process import CommandResult
from accessforge_build_worker.sandbox import DaemonBinding, SandboxRefused

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "apps/build-worker/toolchain"


def test_runtime_lock_is_exact_frozen_reference_app_export() -> None:
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(  # noqa: S603 - read-only export of the committed workspace lock.
        [
            uv,
            "export",
            "--frozen",
            "--package",
            "accessforge-reference-app",
            "--no-dev",
            "--no-emit-workspace",
            "--no-header",
            "--no-annotate",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert (
        result.stdout.decode().strip()
        == (CONTEXT / "runtime-requirements.lock").read_text().strip()
    )


@pytest.mark.parametrize("changed", [False, True])
def test_only_enumerated_context_is_sent_and_daemon_change_refuses_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed: bool,
) -> None:
    for name in toolchain._CONTEXT_FILES:
        shutil.copyfile(CONTEXT / name, tmp_path / name)
    (tmp_path / "DO_NOT_SEND.env").write_text("not part of the trusted context")
    image = "sha256:" + "a" * 64
    calls: list[tuple[str, ...]] = []

    def command(
        executable: str,
        endpoint: str,
        args: tuple[str, ...],
        *,
        deadline: float,
        limit: int,
        input_bytes: bytes = b"",
    ) -> CommandResult:
        assert endpoint == "unix:///test/docker.sock" and deadline > 0 and limit == 1024 * 1024
        calls.append(args)
        if args[0] == "info":
            output = b"changed" if changed and len(calls) == 3 else b"original"
        else:
            assert args[:3] == ("build", "--quiet", "--pull=false")
            assert args[-1] == "-"
            with tarfile.open(fileobj=io.BytesIO(input_bytes)) as archive:
                assert set(archive.getnames()) == set(toolchain._CONTEXT_FILES)
            output = image.encode()
        return CommandResult(0, output, b"")

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(toolchain, "_docker_command", command)
    daemon = DaemonBinding("unix:///test/docker.sock", "original")
    if changed:
        with pytest.raises(SandboxRefused, match="daemon changed"):
            toolchain.provision_reference_toolchain(tmp_path, daemon=daemon)
    else:
        receipt = toolchain.provision_reference_toolchain(tmp_path, daemon=daemon)
        assert receipt.image_id == image and receipt.daemon == daemon
        assert len(receipt.context_digest) == 64
    assert len(calls) == 3


def test_ci_requires_reference_provisioning_and_backend_audit() -> None:
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    steps = [step for job in jobs.values() for step in job.get("steps", [])]
    for name in (
        "Provision the pinned reference-app build toolchain",
        "Audit the hash-locked reference build backend",
    ):
        step = next(step for step in steps if step.get("name") == name)
        assert "if" not in step and not step.get("continue-on-error", False)
    provision = next(
        step
        for step in steps
        if step.get("name") == "Provision the pinned reference-app build toolchain"
    )
    assert "ACCESSFORGE_REFERENCE_TOOLCHAIN=$reference_image" in provision["run"]
    assert '--endpoint "$ACCESSFORGE_SANDBOX_ENDPOINT"' in provision["run"]
    base = (CONTEXT / "Dockerfile").read_text().splitlines()[0].removeprefix("FROM ")
    assert "pull " + base in provision["run"]
