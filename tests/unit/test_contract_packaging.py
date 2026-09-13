"""The build hook must carry schemas across both build modes without requiring runtime Hatchling."""

from __future__ import annotations

import importlib.util
import io
import runpy
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _hook() -> Any:
    spec = importlib.util.spec_from_file_location(
        "contract_build_hook", ROOT / "packages/contracts/python/hatch_build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "target,destination",
    [("sdist", "src/accessforge_contracts/schemas"), ("wheel", "accessforge_contracts/schemas")],
)
def test_shared_schemas_are_included_once(tmp_path: Path, target: str, destination: str) -> None:
    shared = tmp_path / "schemas"
    shared.mkdir()
    (shared / "run-manifest.schema.json").write_text("{}")
    data: dict[str, Any] = {"force_include": {}}
    _hook().initialize(
        SimpleNamespace(root=str(tmp_path / "python"), target_name=target), "standard", data
    )
    assert data == {"force_include": {str(shared): destination}}


def test_wheel_from_sdist_does_not_add_duplicate_files(tmp_path: Path) -> None:
    bundled = tmp_path / "src/accessforge_contracts/schemas"
    bundled.mkdir(parents=True)
    (bundled / "run-manifest.schema.json").write_text("{}")
    data: dict[str, Any] = {"force_include": {}}
    _hook().initialize(SimpleNamespace(root=str(tmp_path), target_name="wheel"), "standard", data)
    assert data == {"force_include": {}}


@pytest.mark.parametrize("bundled", [False, True])
def test_missing_schemas_fail_instead_of_shipping_a_broken_package(
    tmp_path: Path, bundled: bool
) -> None:
    if bundled:
        (tmp_path / "src/accessforge_contracts/schemas").mkdir(parents=True)
    with pytest.raises(ValueError, match="missing"):
        _hook().initialize(
            SimpleNamespace(root=str(tmp_path), target_name="wheel"),
            "standard",
            {"force_include": {}},
        )


@pytest.mark.parametrize("fault", [None, "missing", "substituted", "duplicate"])
def test_archive_verifier_rejects_missing_changed_and_duplicate_resources(
    fault: str | None,
) -> None:
    checker = runpy.run_path(str(ROOT / "scripts/check_core_packages.py"))["_check_wheel"]
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        if fault != "missing":
            archive.writestr(
                "package/schemas/run.json", b"changed" if fault == "substituted" else b"trusted"
            )
        if fault == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr("package/schemas/run.json", b"trusted")
    with zipfile.ZipFile(payload) as archive:
        if fault is None:
            checker(archive, "package/schemas/", {"run.json": b"trusted"})
        else:
            with pytest.raises(ValueError):
                checker(archive, "package/schemas/", {"run.json": b"trusted"})
