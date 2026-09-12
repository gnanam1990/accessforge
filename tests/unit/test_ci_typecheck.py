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
