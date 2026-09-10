"""The declared version matrix against the repository's own pins.

`infra/version-matrix.toml` records what this release was built and tested against, and
`scripts/doctor.py` checks a machine against it. That only means something while the file agrees
with the lockfiles and workflow it claims to describe -- and the way it stops agreeing is not a
malicious edit, it is a routine dependency bump that updates `package.json` and forgets the matrix.
A stale matrix is worse than none: it passes a machine the release was never tested on.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = tomllib.loads((ROOT / "infra" / "version-matrix.toml").read_text(encoding="utf-8"))
PACKAGE_JSON = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_python_matches_the_requires_python_bound() -> None:
    declared = PYPROJECT["project"]["requires-python"]
    python = MATRIX["toolchain"]["python"]
    assert f">={python['min']}" in declared
    assert f"<{python['below']}" in declared


def test_node_matches_the_engines_field() -> None:
    engines = PACKAGE_JSON["engines"]["node"]
    node = MATRIX["toolchain"]["node"]
    assert f">={node['min']}" in engines
    assert f"<{node['below']}" in engines


def test_pnpm_matches_the_package_manager_field() -> None:
    assert PACKAGE_JSON["packageManager"] == f"pnpm@{MATRIX['toolchain']['pnpm']['exact']}"


def test_uv_matches_the_version_ci_installs() -> None:
    """The doctor and CI must agree, or a machine passes locally and fails in the pipeline."""
    versions = re.findall(r'astral-sh/setup-uv@v\d+\s*\n\s*with:\s*\n\s*version:\s*"([^"]+)"', CI)
    assert versions, "could not find the uv version CI installs"
    assert set(versions) == {MATRIX["toolchain"]["uv"]["exact"]}


def test_pnpm_matches_the_version_ci_installs() -> None:
    versions = re.findall(r"pnpm/action-setup@v\d+\s*\n\s*with:\s*\n\s*version:\s*([\d.]+)", CI)
    assert versions, "could not find the pnpm version CI installs"
    assert set(versions) == {MATRIX["toolchain"]["pnpm"]["exact"]}


def test_postgres_matches_the_image_ci_runs() -> None:
    images = re.findall(r"image:\s*postgres:(\d+)", CI)
    assert images, "could not find the postgres image CI runs"
    assert set(images) == {MATRIX["service"]["postgresql"]["min"]}


def test_every_blocked_capability_says_what_would_unblock_it() -> None:
    """A status without a remedy is a shrug.

    Each of these is a person's action -- a permissions grant, a machine, an entitlement -- and the
    doctor prints the text verbatim. If it does not say what to do, the operator reads "BLOCKED"
    and has nowhere to go.
    """
    capabilities = MATRIX["capability"]
    assert capabilities, "the matrix declares no platform capabilities"
    for name, spec in capabilities.items():
        assert spec["status"] in {"BLOCKED", "OK"}, name
        assert spec["platform"] in {"any", "darwin", "win32", "linux"}, name
        assert len(spec["needs"].strip()) > 60, f"{name} does not say what would unblock it"


def test_no_capability_claims_a_container_can_provide_a_screen_reader() -> None:
    """The specific false claim this product exists to refuse, checked in its own inventory."""
    for name in ("voiceover", "nvda"):
        assert MATRIX["capability"][name]["status"] == "BLOCKED"
