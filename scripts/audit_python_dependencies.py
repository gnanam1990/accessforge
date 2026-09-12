"""Audit the locked Python dependency set for known vulnerabilities. Fails closed.

The JavaScript side has had `pnpm audit` in the security job since module 01. The Python side had
nothing, which is the larger surface: the API, the persistence layer, the evidence tooling and the
navigator all run on it.

Four decisions worth reading before changing this.

**The lock is the input, not the environment.** `uv export --frozen` renders `uv.lock` to a fully
pinned requirements set and refuses to re-resolve anything. Auditing an installed environment
instead
would audit whatever the runner happened to resolve, so a lock that pinned a vulnerable version
could
pass on a machine that had something else installed -- and the gate would be reporting on the wrong
artifact while looking green.

**Nothing is installed and no package code runs.** `--no-deps` means pip-audit reads the pinned set
as given rather than resolving it, and `--disable-pip` keeps pip out of the process entirely. Both
matter for the same reason: resolving a dependency can mean downloading an sdist and executing its
`setup.py`, which is arbitrary code from a third party running inside a CI job. The JavaScript audit
has `--ignore-scripts` for exactly this; these two flags are the equivalent.

**Workspace members are excluded, and only they.** `--no-emit-workspace` drops this repository's own
packages, which are editable, have no hashes and appear in no advisory database. Everything
third-party stays in. The exclusion is narrow on purpose: dropping anything else would be a blanket
suppression wearing a flag's clothing.

**There is no ignore list.** Not an empty one -- none. A suppression mechanism is the thing that
gets
used at four in the morning, and the honest alternative when an advisory genuinely does not apply
is a
recorded decision in the lock (a pin, an override) that a reader can see. If one is ever needed it
should arrive with the advisory id, the reason and a date, as code review, not as a flag here.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: A floor on how many packages a real audit covers, so a truncated or empty export cannot pass. The
#: locked set currently resolves to ~96 third-party packages on Linux; this is far below that and
#: far
#: above anything a broken export would produce. Exactness is impossible here without replicating
#: uv's marker evaluation: eleven packages are environment-specific, so the count legitimately
#: differs by platform.
MINIMUM_AUDITED_PACKAGES = 50

#: A pinned requirement line: a name, `==`, a version. Anything looser in the export means the lock
#: stopped being authoritative, which is the failure this whole script is built on.
_PINNED = re.compile(r"^[A-Za-z0-9][^\s;]*==[^\s;]+")

_EXPORT_COMMAND = (
    "uv",
    "export",
    # The lock decides. Without this uv may re-resolve, and the audited set stops being the set that
    # ships.
    "--frozen",
    "--format",
    "requirements-txt",
    # This repository's own packages: editable, unhashed, in no advisory database.
    "--no-emit-workspace",
)

_AUDIT_COMMAND = (
    "pip-audit",
    "--no-deps",
    "--disable-pip",
    "--strict",
    "--format",
    "json",
    "--requirement",
)


#: Ways pip-audit can be told to ignore a finding. Checked against *executable code*, never the
#: whole file: this module's own prose names these flags in order to forbid them, and a check that
#: matched its own explanation would fail for the wrong reason -- turning "we document our
#: guarantees" into "CI is red". See `suppression_in_code`.
SUPPRESSION_TOKENS = ("--ignore-vuln", "--ignore-vulns", "ignore_vuln", "ignore_vulns")


def suppression_in_code(source: str) -> list[str]:
    """Any suppression mechanism reachable at runtime, ignoring prose that merely names one.

    Parses and re-renders the module with docstrings removed. `ast.unparse` drops comments on its
    own, so what is left is code: string literals that are actually arguments, and identifiers that
    are actually keywords. A flag named in a docstring or a `#` comment -- which is how this
    codebase explains every guard it has -- is invisible here. A flag passed to a subprocess is not.

    The alternative, a substring search over the file, is what the first version did. It passed only
    because the prose happened not to use the exact spelling; one clarifying comment would have made
    the security job fail and the fix would have looked like deleting the check.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body.pop(0)
    # This module names the tokens in a constant, which is code. Dropping the assignment that
    # declares them is not a loophole: a real suppression would also appear somewhere else, in the
    # command it is passed to.
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SUPPRESSION_TOKENS"
                for target in node.targets
            )
        )
    ]
    executable = ast.unparse(tree)
    return [token for token in SUPPRESSION_TOKENS if token in executable]


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason the gate fails. Always printed; never counted and summarised away."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def assess(report: dict[str, Any], requirements: str) -> list[Finding]:
    """Decide whether this audit may pass, from the report and the input it ran on.

    Pure, so the decision is testable without a network or a subprocess -- which matters because the
    cases worth testing are the ones that must *not* pass, and a green audit of a healthy lock
    exercises none of them.

    Four independent ways to fail, because a gate with one check has one way to be bypassed:

    1. **An advisory.** Every one, at every severity. pip-audit reports no severity field for many
       ecosystems, so a severity threshold here would silently drop the advisories it could not
       grade.
    2. **Too few packages.** A truncated or empty export otherwise passes with nothing to say.
    3. **An unpinned requirement.** Then the audited version is not the version that ships.
    4. **A missing hash.** Then the pinned version is a name, not an artifact: the same version
       string can be republished.
    """
    findings: list[Finding] = []

    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        return [
            Finding(
                "unreadable report",
                "pip-audit produced no `dependencies` array, so nothing was established. Refusing "
                "rather than treating an unparseable report as a clean one.",
            )
        ]

    for package in dependencies:
        for vulnerability in package.get("vulns") or []:
            fixes = vulnerability.get("fix_versions") or []
            findings.append(
                Finding(
                    "advisory",
                    f"{package.get('name')} {package.get('version')} is affected by "
                    f"{vulnerability.get('id')}"
                    + (
                        f"; fixed in {', '.join(fixes)}"
                        if fixes
                        else "; no fixed version published"
                    ),
                )
            )

    if len(dependencies) < MINIMUM_AUDITED_PACKAGES:
        findings.append(
            Finding(
                "wrong input",
                f"only {len(dependencies)} package(s) were audited, below the floor of "
                f"{MINIMUM_AUDITED_PACKAGES}. An export that produced almost nothing would "
                "otherwise pass with nothing to report.",
            )
        )

    specs = [
        line
        for line in requirements.splitlines()
        if line.strip() and not line.startswith(("#", " ", "-"))
    ]
    unpinned = [line.strip() for line in specs if not _PINNED.match(line.strip())]
    if unpinned:
        findings.append(
            Finding(
                "unlocked requirement",
                f"{len(unpinned)} requirement(s) are not pinned to an exact version, so the "
                f"audited version is not what ships: {', '.join(unpinned[:5])}",
            )
        )

    if "--hash=sha256:" not in requirements:
        findings.append(
            Finding(
                "missing provenance",
                "the exported requirements carry no hashes, so a pinned version names a string "
                "rather than an artifact and the same version can be republished.",
            )
        )

    return findings


def _run(
    command: tuple[str, ...], *, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied words
        command, capture_output=True, text=True, input=stdin_text, check=False
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        help="Audit this requirements file instead of exporting the lock. For tests only.",
    )
    arguments = parser.parse_args(argv)

    if arguments.requirements is not None:
        requirements = arguments.requirements.read_text(encoding="utf-8")
        export_path = arguments.requirements
    else:
        exported = _run(_EXPORT_COMMAND)
        if exported.returncode != 0:
            print("::error::could not export uv.lock; the audited set is unknown", file=sys.stderr)
            print(exported.stderr.strip()[:2000], file=sys.stderr)
            return 1
        requirements = exported.stdout
        # A temporary file, not the working tree. pip-audit needs a path, but the rendered lock is
        # derived state: writing it next to uv.lock would commit a second source of truth that goes
        # stale silently, and a reviewer comparing the two could not tell which the gate had used.
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", prefix="locked-requirements-", delete=False, encoding="utf-8"
        )
        with handle:
            handle.write(requirements)
        export_path = Path(handle.name)

    try:
        return _audit(export_path, requirements)
    finally:
        if arguments.requirements is None:
            export_path.unlink(missing_ok=True)


def _audit(export_path: Path, requirements: str) -> int:
    audited = _run((*_AUDIT_COMMAND, str(export_path)))
    try:
        report = json.loads(audited.stdout)
    except json.JSONDecodeError:
        # pip-audit exits non-zero both for "vulnerabilities found" and for "could not run", and
        # only the first produces a report. An unparseable stdout means the scan did not happen,
        # which must fail rather than read as nothing found.
        print(
            "::error::pip-audit produced no JSON report, so no audit was performed", file=sys.stderr
        )
        print((audited.stderr or audited.stdout).strip()[:2000], file=sys.stderr)
        return 1

    findings = assess(report, requirements)
    for finding in findings:
        print(f"::error::{finding}")

    audited_count = len(report.get("dependencies") or [])
    if findings:
        print(
            f"\\n{len(findings)} problem(s) across {audited_count} audited package(s). "
            "Not suppressible here: fix the dependency, or record the decision in the lock.",
            file=sys.stderr,
        )
        return 1

    print(
        f"{audited_count} locked third-party package(s) audited against the advisory database; "
        "no known vulnerabilities. The set came from uv.lock with --frozen, nothing was installed "
        "and no package code ran."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
