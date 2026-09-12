"""The tests and operator tools must remain in the strict CI type-check boundary."""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ci_typechecks_the_test_suite_and_all_python_workspace_members() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["python"]["steps"]
    step = next(step for step in steps if step.get("name") == "Type check (strict)")
    argv = shlex.split(step["run"])
    assert argv[:3] == ["uv", "run", "mypy"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    sources = {f"{member}/src" for member in project["tool"]["uv"]["workspace"]["members"]}
    assert set(argv[3:]) == sources | {"scripts", "tests"}
    assert not step.get("continue-on-error", False)
    assert "if" not in step


def test_strict_config_does_not_exempt_the_test_suite() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mypy"]
    assert config["strict"] is True
    assert "exclude" not in config
    # The only exemptions are existing untyped S3 SDKs, never application or test modules.
    assert config["overrides"] == [
        {"module": ["boto3.*", "botocore.*"], "ignore_missing_imports": True}
    ]


def test_ci_requires_provisioned_real_sandbox_probes() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["python"]
    image = job["env"]["ACCESSFORGE_SANDBOX_IMAGE"]
    assert "@sha256:" in image
    steps = job["steps"]
    provision = next(
        s
        for s in steps
        if s.get("name") == "Provision the pinned owned-build containment probe toolchain"
    )
    probe = next(s for s in steps if s.get("name") == "Real owned-build containment probes")
    assert shlex.split(provision["run"]) == ["docker", "pull", image]
    assert shlex.split(probe["run"]) == ["uv", "run", "pytest", "-q", "tests/sandbox", "--tb=short"]
    assert steps.index(provision) < steps.index(probe)
    for step in (provision, probe):
        assert "if" not in step
        assert not step.get("continue-on-error", False)
