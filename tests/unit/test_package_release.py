"""Release packaging checks use disposable source trees, never services or actual readers."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from package_release import ROOT, git, package  # noqa: E402


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    for name in ("pyproject.toml", "uv.lock", "pnpm-lock.yaml", "infra/version-matrix.toml"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic release input\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Packaging Test",
        "-c",
        "user.email=packaging@example.invalid",
        "commit",
        "-m",
        "synthetic source",
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>synthetic static assets</html>")
    (web / "assets").mkdir()
    (web / "assets/app.js").write_text("void 0;")
    return root, web


def test_release_inventory_reproduces_and_omits_untracked_secrets(
    inputs: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    root, web = inputs
    (root / ".env").write_text("synthetic-private-marker")
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    checksum = package(root, web, first)
    assert package(root, web, second) == checksum
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["sourceCommit"] == git(root, "rev-parse", "HEAD").decode().strip()
        assert manifest["actualReaderQualified"] is False
        assert manifest["deploymentVerified"] is False
        assert "source/.env" not in archive.namelist()
        assert {item["path"] for item in manifest["files"]} == set(archive.namelist()) - {
            "manifest.json"
        }
        for item in manifest["files"]:
            data = archive.read(item["path"])
            assert len(data) == item["size"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
    with pytest.raises(ValueError, match="already exists"):
        package(root, web, first)
    assert hashlib.sha256(first.read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_tracked_source_refuses_before_output(
    inputs: tuple[Path, Path],
    tmp_path: Path,
    staged: bool,
) -> None:
    root, web = inputs
    (root / "uv.lock").write_text("changed")
    if staged:
        git(root, "add", "uv.lock")
    output = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="clean tracked"):
        package(root, web, output)
    assert not output.exists()


def test_unbuilt_web_and_symlinks_refuse(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    root, web = inputs
    output = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="built web"):
        package(root, tmp_path / "missing", output)
    (web / "external").symlink_to(root / "uv.lock")
    with pytest.raises(ValueError, match="symlinks"):
        package(root, web, output)
    assert not output.exists()


def test_release_workflow_builds_after_ci_without_deployment() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["package"]
    assert job["needs"] == "verified"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--frozen-lockfile --ignore-scripts" in commands
    assert commands.index("@accessforge/web build") < commands.index("package_release.py")
    assert "uv sync --frozen --no-dev --project rehearsal/source" in commands
    assert "python -I rehearsal/source/scripts/check_release_install.py" in commands
    install_index = next(
        i
        for i, step in enumerate(job["steps"])
        if "check_release_install.py" in step.get("run", "")
    )
    upload_index = next(
        i for i, step in enumerate(job["steps"]) if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert install_index < upload_index
    upload = next(step for step in job["steps"] if step.get("uses") == "actions/upload-artifact@v4")
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"].split() == [
        "accessforge-release.zip",
        "accessforge-release.sha256",
    ]
