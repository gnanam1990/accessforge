"""What a repair patch may touch, and what it may never touch.

FR-010 allows an agent to propose a source change. The danger is not that the change is wrong --
a wrong change fails verification -- but that it is *right about the wrong thing*. The cheapest way
to make a failing accessibility assertion pass is to edit the assertion. The second cheapest is to
remove the validation that produced the error being announced. Both would verify cleanly, and both
make the product worse while reporting success.

So the policy here is not a lint. It is the list of edits that end the proposal:

* **Protected tests and assertions.** A repair that edits the test is not a repair.
* **Authorization, consent and security policy.** "The form submits now" is trivially achievable by
  deleting the permission check.
* **Validation.** Removing the check that produces "Email, invalid entry" removes the error the
  journey is about. The announcement gets fixed by deleting the thing being announced.
* **The evaluator and the fixture oracle.** These decide whether the repair worked. A patch that
  can reach them is a patch that can grade itself (INV-03).
* **CI configuration and secrets, and execution tooling.** A patch that edits the pipeline chooses
  how it is checked.

Two further categories are refused on shape alone, regardless of where they point: traversal and
absolute paths, which leave the candidate workspace, and symlinks, which leave it without looking
like they do (INV-16).

Dependency manifests and lockfiles are **not** refused. They are real accessibility fixes sometimes
-- a component library upgrade can be exactly the repair. But they are not routine edits, they
change what gets built from what, and a reviewer reading "fixed the label" deserves to be told that
the lockfile moved. They come back as `separately_reviewed`, and proposing them requires saying so.

Pure policy, no I/O. The patch never reaches a filesystem here: this decides on paths and content
that a caller has already read, which is what makes it testable without a sandbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

#: Git's mode for a symbolic link. A patch creating one inside the candidate workspace can point
#: anywhere the build user can read -- the host's credentials included -- while every path in the
#: diff stays inside the tree.
SYMLINK_MODE = "120000"


class ChangeVerdict(StrEnum):
    """What the policy says about one changed path."""

    ALLOWED = "ALLOWED"
    REFUSED = "REFUSED"
    SEPARATELY_REVIEWED = "SEPARATELY_REVIEWED"


#: Paths no repair may modify, with the reason a reader needs. Ordered: the first match wins, so
#: the more specific patterns come first.
#:
#: Matched against the whole POSIX path, case-insensitively. Deliberately broad -- a false refusal
#: costs a conversation, and a false allowance costs the product's only claim.
PROTECTED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"(^|/)(tests?|spec|specs|__tests__|e2e|cypress|playwright)(/|$)",
        "protected tests. A repair that edits the test it must satisfy is not a repair, and this "
        "is the single cheapest way to make a failing assertion pass",
    ),
    (
        r"(^|/)[^/]*(_test|_spec|\.test|\.spec)\.[a-z0-9]+$",
        "a protected test file, for the same reason",
    ),
    (
        r"(^|/)test_[^/]*\.py$",
        "a protected test file, for the same reason",
    ),
    (
        r"(^|/)[^/]*(authoriz|authentic|permission|entitlement|\brbac\b)[^/]*",
        "authorization. 'The submission succeeds now' is trivially achievable by deleting the "
        "permission check, and that change would verify cleanly",
    ),
    (
        r"(^|/)[^/]*(consent|privacy|gdpr)[^/]*",
        "consent or privacy policy. Neither is an accessibility defect and neither may be edited "
        "to make one easier to fix",
    ),
    (
        r"(^|/)[^/]*(valid|sanitiz|sanitis)[^/]*",
        "input validation. The journey under test is about the error message a validator produces; "
        "deleting the validator removes the error instead of announcing it properly",
    ),
    (
        r"(^|/)(\.github|\.gitlab-ci\.yml|\.circleci|azure-pipelines\.yml|Jenkinsfile)(/|$)",
        "continuous integration configuration. A patch that edits the pipeline chooses how it is "
        "checked",
    ),
    (
        r"(^|/)[^/]*(secret|credential|\.pem|\.key|id_rsa)[^/]*",
        "credentials. Nothing about an accessibility repair requires touching them",
    ),
    (
        r"(^|/)\.env",
        "environment configuration, which selects the backend a candidate talks to",
    ),
    (
        r"(^|/)(Dockerfile|docker-compose[^/]*|Makefile|justfile|Taskfile[^/]*)$",
        "execution tooling, which decides how the candidate is built and run",
    ),
    (
        r"(^|/)(packages/(domain|evidence)|apps/(api|orchestrator))/",
        "AccessForge's own evaluator and evidence code. A patch that can reach the thing deciding "
        "whether it worked is a patch that grades itself (INV-03)",
    ),
    (
        r"(^|/)[^/]*(fixture|oracle)[^/]*",
        "the fixture oracle, which defines what the journey expects. Changing it changes the task "
        "rather than completing it",
    ),
)

#: Dependency and build description. Real repairs sometimes live here, so these are reported rather
#: than refused -- but never as a routine accessibility edit.
SEPARATE_REVIEW_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|npm-shrinkwrap\.json"
        r"|uv\.lock|poetry\.lock|Pipfile\.lock|Gemfile\.lock|Cargo\.lock|go\.sum"
        r"|composer\.lock)$",
        "a dependency lockfile: it changes the exact bytes the candidate is built from",
    ),
    (
        r"(^|/)(package\.json|pyproject\.toml|requirements[^/]*\.txt|Gemfile|Cargo\.toml"
        r"|go\.mod|composer\.json|build\.gradle[^/]*|pom\.xml)$",
        "a dependency manifest: it changes what the candidate is built from",
    ),
    (
        r"(^|/)(tsconfig[^/]*\.json|vite\.config\.[a-z]+|webpack\.config\.[a-z]+"
        r"|rollup\.config\.[a-z]+|babel\.config\.[a-z]+)$",
        "build policy: it changes how the candidate is produced",
    ),
)


@dataclass(frozen=True, slots=True)
class ProposedChange:
    """One file a patch proposes to change.

    `content` is the post-change text, or None for a deletion. `mode` is the git file mode when the
    caller knows it, which is how a symlink is recognised -- the path and diff of a symlink addition
    look like an ordinary small text file.
    """

    path: str
    content: str | None = None
    mode: str | None = None
    binary: bool = False


@dataclass(frozen=True, slots=True)
class ChangeRuling:
    """What the policy decided about one change, and why."""

    path: str
    verdict: ChangeVerdict
    reason: str


@dataclass(frozen=True, slots=True)
class PatchInspection:
    """The ruling on a whole patch.

    `refusals` being non-empty is the end of the proposal. `separately_reviewed` is not -- it is the
    list a reviewer must be shown, and the caller has to acknowledge it rather than letting it pass
    as a routine edit.
    """

    rulings: tuple[ChangeRuling, ...] = field(default_factory=tuple)

    @property
    def refusals(self) -> tuple[ChangeRuling, ...]:
        return tuple(r for r in self.rulings if r.verdict is ChangeVerdict.REFUSED)

    @property
    def separately_reviewed(self) -> tuple[ChangeRuling, ...]:
        return tuple(r for r in self.rulings if r.verdict is ChangeVerdict.SEPARATELY_REVIEWED)

    @property
    def allowed(self) -> tuple[ChangeRuling, ...]:
        return tuple(r for r in self.rulings if r.verdict is ChangeVerdict.ALLOWED)

    @property
    def acceptable(self) -> bool:
        """Whether this patch may be recorded. Says nothing about whether it is a good fix."""
        return not self.refusals and bool(self.rulings)

    def explain(self) -> str:
        """The rulings in words, for a reviewer rather than a log.

        Refusals first, because they are the decision; the counts follow so a reader can see the
        shape of the patch without reading every path.
        """
        if not self.rulings:
            return "the patch changes nothing, so there is nothing to review"
        lines = [f"refused: {r.path} -- {r.reason}" for r in self.refusals]
        lines += [
            f"needs separate review: {r.path} -- {r.reason}" for r in self.separately_reviewed
        ]
        lines.append(
            f"{len(self.allowed)} application path(s) allowed, {len(self.refusals)} refused, "
            f"{len(self.separately_reviewed)} needing separate review"
        )
        return "\n".join(lines)


def _matches(path: str, patterns: tuple[tuple[str, str], ...]) -> str | None:
    for pattern, reason in patterns:
        if re.search(pattern, path, flags=re.IGNORECASE):
            return reason
    return None


def _shape_refusal(change: ProposedChange) -> str | None:
    """Refusals that depend on the shape of the path, not on where it points."""
    path = change.path
    if not path or path != path.strip():
        return "an empty or space-padded path, which names no file a reviewer could check"
    if path.startswith("/"):
        return "an absolute path, which is outside the candidate workspace by construction"
    if path.startswith("~"):
        return "a home-relative path, which is outside the candidate workspace"
    if "\\" in path:
        return (
            "a backslash in the path. Windows separators are not normalised here, so this would be "
            "one filename on the proposing side and a directory traversal on the applying side"
        )
    if "\x00" in path:
        return "a null byte in the path"
    segments = path.split("/")
    if ".." in segments:
        return "path traversal, which writes outside the candidate workspace (INV-16)"
    if change.mode == SYMLINK_MODE:
        return (
            "a symbolic link. Every path in the diff stays inside the tree while the link points "
            "wherever the build user can read -- the host's credentials included (INV-16)"
        )
    if change.binary:
        return (
            "a binary replacement. A reviewer cannot read it, so approving it approves bytes "
            "nobody has seen"
        )
    return None


def inspect_patch(
    changes: tuple[ProposedChange, ...], *, application_paths: tuple[str, ...] = ()
) -> PatchInspection:
    """Rule on every change in a proposed patch.

    `application_paths` is the project's declared repair surface. When it is empty every
    non-protected path is treated as application code, which is the E0 single-owned-fixture case.
    When it is set, a path outside it is refused: an agent reaching outside the surface somebody
    authorized is the failure this argument exists for, and "it was only a small file" is how that
    gets waved through.

    Order matters. Shape refusals come first because a traversing path must not be excused by
    happening to sit under an allowed prefix, and protection beats the allowlist because a project
    cannot declare its own tests repairable.
    """
    rulings: list[ChangeRuling] = []
    for change in changes:
        shape = _shape_refusal(change)
        if shape is not None:
            rulings.append(ChangeRuling(change.path, ChangeVerdict.REFUSED, shape))
            continue

        protected = _matches(change.path, PROTECTED_PATTERNS)
        if protected is not None:
            rulings.append(ChangeRuling(change.path, ChangeVerdict.REFUSED, protected))
            continue

        if application_paths and not any(
            change.path == prefix or change.path.startswith(prefix.rstrip("/") + "/")
            for prefix in application_paths
        ):
            rulings.append(
                ChangeRuling(
                    change.path,
                    ChangeVerdict.REFUSED,
                    "outside the repair surface this project declared "
                    f"({', '.join(application_paths)})",
                )
            )
            continue

        separate = _matches(change.path, SEPARATE_REVIEW_PATTERNS)
        if separate is not None:
            rulings.append(ChangeRuling(change.path, ChangeVerdict.SEPARATELY_REVIEWED, separate))
            continue

        rulings.append(
            ChangeRuling(change.path, ChangeVerdict.ALLOWED, "application code within the surface")
        )
    return PatchInspection(tuple(rulings))
