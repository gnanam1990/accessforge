"""The Python dependency gate, and the ways it could go green while auditing nothing.

The JavaScript side has had `pnpm audit` in the security job since module 01; the Python side had
nothing, which is the larger surface. A gate is only worth the cases it refuses, so these tests are
almost entirely about inputs that must fail: a clean audit of a healthy lock exercises none of the
interesting paths, and a gate with one check has one way to be bypassed.

`assess` is pure, so the decision is testable without a network, a subprocess or an advisory
database
that changes under the suite. The workflow guards are asserted against the real `ci.yml` and the
real
script, because a gate nobody invokes is the failure mode that matters most.

Requirements: the module 26 security posture. Scope: CI.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

from audit_python_dependencies import (  # noqa: E402
    MINIMUM_AUDITED_PACKAGES,
    SUPPRESSION_TOKENS,
    assess,
    suppression_in_code,
)

REPO = pathlib.Path(__file__).resolve().parents[2]

#: A requirements body that satisfies every input check, so each test can break exactly one thing.
HEALTHY_REQUIREMENTS = "\n".join(
    [f"package-{n}==1.0.0 --hash=sha256:{'a' * 64}" for n in range(MINIMUM_AUDITED_PACKAGES)]
)


def _report(
    count: int = MINIMUM_AUDITED_PACKAGES, **vulnerable: list[dict[str, object]]
) -> dict[str, Any]:
    """A pip-audit report shape with `count` clean packages, plus any named vulnerable ones."""
    dependencies: list[dict[str, object]] = [
        {"name": f"package-{n}", "version": "1.0.0", "vulns": []} for n in range(count)
    ]
    for name, vulns in vulnerable.items():
        dependencies.append({"name": name, "version": "1.0.0", "vulns": vulns})
    return {"dependencies": dependencies}


def test_a_clean_locked_set_passes() -> None:
    """The control. Without it, every test below could pass against a gate refusing everything."""
    assert assess(_report(), HEALTHY_REQUIREMENTS) == []


def test_a_single_advisory_fails_the_gate() -> None:
    """Every advisory, at every severity.

    pip-audit reports no severity for many ecosystems, so a severity threshold here would silently
    drop the advisories it could not grade -- and the ones it cannot grade are not the safe ones.
    """
    findings = assess(
        _report(vulnerable_pkg=[{"id": "GHSA-xxxx-yyyy-zzzz", "fix_versions": ["1.0.1"]}]),
        HEALTHY_REQUIREMENTS,
    )
    assert len(findings) == 1
    assert findings[0].kind == "advisory"
    assert "GHSA-xxxx-yyyy-zzzz" in findings[0].detail
    # The fix is named, because an advisory a reader cannot act on becomes an advisory somebody
    # suppresses.
    assert "fixed in 1.0.1" in findings[0].detail


def test_an_advisory_with_no_published_fix_still_fails_and_says_so() -> None:
    """The case that most invites a suppression, so it has to be reported precisely."""
    findings = assess(
        _report(vulnerable_pkg=[{"id": "GHSA-nofix", "fix_versions": []}]), HEALTHY_REQUIREMENTS
    )
    assert len(findings) == 1
    assert "no fixed version published" in findings[0].detail


def test_every_advisory_is_reported_not_just_the_first() -> None:
    """A count summarised to one line is how the second advisory gets missed."""
    findings = assess(
        _report(
            first=[{"id": "GHSA-a", "fix_versions": []}, {"id": "GHSA-b", "fix_versions": []}],
            second=[{"id": "GHSA-c", "fix_versions": []}],
        ),
        HEALTHY_REQUIREMENTS,
    )
    assert {f.detail.split()[-1] for f in findings} >= {"published"}
    assert len([f for f in findings if f.kind == "advisory"]) == 3


def test_an_almost_empty_audit_fails_as_wrong_input() -> None:
    """A truncated or empty export otherwise passes with nothing to report.

    This is the quietest way a dependency gate dies: the scan runs, finds nothing because it was
    given nothing, and reports success.
    """
    findings = assess(_report(count=3), HEALTHY_REQUIREMENTS)
    assert [f.kind for f in findings] == ["wrong input"]
    assert "below the floor" in findings[0].detail


def test_an_unparseable_report_fails_rather_than_reading_as_clean() -> None:
    """pip-audit exits non-zero both for findings and for failing to run.

    Only the first produces a report, so a missing `dependencies` array means no audit happened --
    which must not be indistinguishable from a clean one.
    """
    findings = assess({"error": "something went wrong"}, HEALTHY_REQUIREMENTS)
    assert [f.kind for f in findings] == ["unreadable report"]


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("package-x>=1.0.0", "a range, so the audited version is not the version that ships"),
        ("package-x", "no version at all"),
        ("package-x~=1.0", "a compatible-release clause"),
    ],
)
def test_an_unpinned_requirement_fails(line: str, why: str) -> None:
    """The lock stops being authoritative the moment a requirement is a range."""
    findings = assess(_report(), HEALTHY_REQUIREMENTS + f"\n{line}")
    kinds = [f.kind for f in findings]
    assert "unlocked requirement" in kinds, why
    assert line in next(f for f in findings if f.kind == "unlocked requirement").detail


def test_requirements_without_hashes_fail_as_missing_provenance() -> None:
    """A pinned version with no hash names a string, not an artifact.

    The same version can be republished, so the thing audited and the thing installed are only
    related by a name somebody else controls.
    """
    unhashed = "\n".join(f"package-{n}==1.0.0" for n in range(MINIMUM_AUDITED_PACKAGES))
    findings = assess(_report(), unhashed)
    assert [f.kind for f in findings] == ["missing provenance"]


def test_the_failures_are_reported_together_rather_than_one_at_a_time() -> None:
    """A caller fixing one problem should not have to re-run to discover the next two."""
    findings = assess(
        _report(count=2, bad=[{"id": "GHSA-q", "fix_versions": []}]),
        "package-x>=1\n",
    )
    assert {f.kind for f in findings} == {
        "advisory",
        "wrong input",
        "unlocked requirement",
        "missing provenance",
    }


# --- the gate exists, is wired, and cannot be softened -------------------------------------------


def _security_steps() -> list[dict[str, object]]:
    document = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    return list(document["jobs"]["security"]["steps"])


def _audit_step() -> dict[str, object]:
    """The audit step, excluding the self-check whose own source mentions the script.

    That exclusion is not pedantry: the first version of the workflow guard matched itself and
    failed
    on its own source, which is the same mistake this project made once before with a grep over raw
    YAML.
    """
    candidates = [
        step
        for step in _security_steps()
        if "audit_python_dependencies.py" in str(step.get("run", ""))
        and "safe_load" not in str(step.get("run", ""))
    ]
    assert len(candidates) == 1, f"expected exactly one audit step, found {len(candidates)}"
    return candidates[0]


def test_the_security_job_runs_the_python_dependency_audit() -> None:
    """A gate nobody invokes is the failure mode that matters most."""
    step = _audit_step()
    assert "uv run python scripts/audit_python_dependencies.py" in str(step["run"])


def test_the_audit_step_cannot_fail_open() -> None:
    """`continue-on-error` or a swallowed exit status makes a red gate green."""
    step = _audit_step()
    assert not step.get("continue-on-error")
    run = str(step["run"])
    for swallow in ("|| true", "|| :", "; true", "set +e", "continue-on-error"):
        assert swallow not in run, f"the audit swallows its exit status with `{swallow}`"


@pytest.mark.parametrize(
    ("flag", "why"),
    [
        ("--frozen", "the export could re-resolve, so the audited set would not be the locked set"),
        ("--no-deps", "pip-audit could resolve dependencies, executing third-party setup code"),
        ("--disable-pip", "pip could be invoked, executing third-party setup code"),
        ("--strict", "pip-audit could skip a package it could not read and still exit zero"),
    ],
)
def test_the_scan_keeps_the_flags_that_make_it_locked_and_script_free(flag: str, why: str) -> None:
    script = (REPO / "scripts/audit_python_dependencies.py").read_text()
    assert f'"{flag}"' in script, f"{flag} was removed: {why}"


@pytest.mark.parametrize("suppressor", SUPPRESSION_TOKENS)
def test_there_is_no_suppression_mechanism(suppressor: str) -> None:
    """Not an empty ignore list -- none.

    A suppression mechanism is the thing that gets used at four in the morning. The honest
    alternative, when an advisory genuinely does not apply, is a recorded decision in the lock
    that a reader can see.
    """
    script = (REPO / "scripts/audit_python_dependencies.py").read_text()
    assert suppressor not in suppression_in_code(script)


@pytest.mark.parametrize(
    "prose",
    [
        '"""Never pass --ignore-vuln or --ignore-vulns to this."""',
        "x = 1  # --ignore-vuln is forbidden here",
        '"""Docstring.\n\nignore_vuln is not available.\n"""\nx = 1',
    ],
)
def test_explanatory_prose_naming_a_flag_is_not_a_suppression(prose: str) -> None:
    """Documenting the guarantee must not break the build that enforces it.

    The first version searched the whole file, so writing "never pass --ignore-vuln" in a comment
    would have turned the security job red -- and the obvious fix would have looked like deleting
    the check. In a codebase where every guard explains itself, that is one clarifying
    sentence away.
    """
    assert suppression_in_code(prose) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('cmd = ("pip-audit", "--ignore-vuln", "GHSA-1")', "--ignore-vuln"),
        ('cmd = ["pip-audit", "--ignore-vulns"]', "--ignore-vulns"),
        ("audit(ignore_vuln=True)", "ignore_vuln"),
        ('subprocess.run(["pip-audit", *extra, "--ignore-vuln"])', "--ignore-vuln"),
        ('FLAGS = "--ignore-vuln"', "--ignore-vuln"),
    ],
)
def test_a_suppression_reachable_at_runtime_is_caught(code: str, expected: str) -> None:
    """The other direction, which is the whole point of the check.

    Ignoring prose must not mean ignoring the flag: an argument in a command, an element of a
    list, a keyword, and a constant holding the flag are all reachable at runtime and all fail.
    """
    assert expected in suppression_in_code(code)


def test_the_workflow_guard_uses_the_same_detector_rather_than_a_substring_search() -> None:
    """One implementation, so CI and this suite cannot disagree about what counts.

    A second copy in the workflow is what produced the original bug: the inline guard searched raw
    text while this file stripped comments, so the two disagreed and only one of them ran in CI.
    """
    workflow = (REPO / ".github/workflows/ci.yml").read_text()
    assert "from audit_python_dependencies import suppression_in_code" in workflow
    assert "suppression_in_code(script)" in workflow
    # And the raw-substring form is gone, not merely supplemented.
    assert "if forbidden in script" not in workflow


def test_the_scanner_itself_is_pinned_in_the_lock() -> None:
    """A scanner resolved fresh on every run is one whose behaviour changes without a commit.

    In `uv.lock`, so the version *and* its artifact hashes are fixed like every other dependency --
    which is a stronger claim than a version string in a workflow file.
    """
    lock = (REPO / "uv.lock").read_text()
    assert 'name = "pip-audit"' in lock
    block = lock.split('name = "pip-audit"', 1)[1][:4000]
    assert re.search(r'version = "\d+\.\d+', block), "pip-audit is in the lock without a version"
    assert "wheels = [" in block or "sdist = {" in block, "pip-audit is locked without artifacts"


def test_the_audit_runs_before_the_tests_that_execute_dependency_code() -> None:
    """Ordering is the point of putting it first in this job.

    Every later step in the security job runs code from the dependency tree -- the test suites, the
    adversarial probes. Auditing afterwards would mean an advisory is reported only once the
    vulnerable code has already executed in CI, which is a report rather than a gate.
    """
    steps = _security_steps()
    audit_at = [
        index
        for index, step in enumerate(steps)
        if "audit_python_dependencies.py" in str(step.get("run", ""))
        and "safe_load" not in str(step.get("run", ""))
    ]
    running_dependency_code = [
        index for index, step in enumerate(steps) if "pytest" in str(step.get("run", ""))
    ]
    assert len(audit_at) == 1
    assert running_dependency_code, "this test assumes the security job runs tests after the audit"
    assert audit_at[0] < min(running_dependency_code), (
        "the audit runs after code from the dependency tree has already executed in CI"
    )
