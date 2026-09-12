"""What a repair patch may not touch.

Every case here is a change that would make a failing accessibility assertion pass while making the
product worse. That is the whole risk of FR-010: a wrong patch fails verification and costs nothing,
but a patch that is right about the wrong thing verifies cleanly.

Pure policy, so these are unit tests. The paths are strings and the content is text; nothing touches
a filesystem, which is what makes the boundary testable without the containment this deployment does
not have.

Requirements: FR-010. Invariants: INV-03, INV-16.
"""

from __future__ import annotations

import pytest

from accessforge_domain.patch_policy import (
    SYMLINK_MODE,
    ChangeVerdict,
    ProposedChange,
    inspect_patch,
)


def _ruling(path: str, **kwargs: object) -> ChangeVerdict:
    inspection = inspect_patch((ProposedChange(path=path, **kwargs),))  # type: ignore[arg-type]
    assert len(inspection.rulings) == 1
    return inspection.rulings[0].verdict


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_checkout.py",
        "src/components/__tests__/Form.spec.tsx",
        "e2e/checkout.spec.ts",
        "app/models/user_test.go",
        "spec/requests/orders_spec.rb",
    ],
)
def test_a_patch_may_not_edit_the_tests_it_must_satisfy(path: str) -> None:
    """The single cheapest way to make a failing assertion pass."""
    assert _ruling(path) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path",
    [
        "src/auth/authorization.ts",
        "app/permissions.py",
        "lib/authentication/session.rb",
        "src/rbac/policy.go",
    ],
)
def test_a_patch_may_not_edit_authorization(path: str) -> None:
    """ "The submission succeeds now" is trivially achievable by deleting the permission check."""
    assert _ruling(path) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path", ["src/forms/validation.js", "app/validators/email.rb", "lib/sanitize.ts"]
)
def test_a_patch_may_not_remove_the_validation_the_journey_is_about(path: str) -> None:
    """The journey is about the error a validator produces.

    Deleting the validator removes the error instead of announcing it properly, and every
    accessibility assertion about that error then passes by vacuum.
    """
    assert _ruling(path) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "Jenkinsfile",
        "Dockerfile",
        "Makefile",
        ".env.production",
        "config/secrets.yml",
        "deploy/id_rsa",
    ],
)
def test_a_patch_may_not_choose_how_it_is_checked_or_what_it_talks_to(path: str) -> None:
    assert _ruling(path) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path",
    [
        "packages/domain/src/accessforge_domain/outcome.py",
        "apps/api/src/accessforge_api/routes/runs.py",
        "fixtures/reference-app/src/oracle.ts",
    ],
)
def test_a_patch_may_not_reach_the_thing_that_grades_it(path: str) -> None:
    """INV-03. A patch that can edit the evaluator or the fixture oracle grades itself."""
    assert _ruling(path) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "src/../../outside.ts",
        "/etc/hosts",
        "~/.aws/credentials",
        "src\\windows\\path.ts",
    ],
)
def test_a_patch_may_not_leave_the_candidate_workspace(path: str) -> None:
    """INV-16. Refused on the shape of the path, wherever it happens to point."""
    assert _ruling(path) is ChangeVerdict.REFUSED


def test_a_symlink_is_refused_even_though_its_path_looks_ordinary() -> None:
    """The path stays inside the tree; the link does not.

    This is why mode is inspected and not just the path: a symlink addition's diff looks like a
    small text file, and the file it resolves to can be anything the build user can read.
    """
    assert _ruling("src/assets/logo.svg", mode=SYMLINK_MODE) is ChangeVerdict.REFUSED
    # The same path without the symlink mode is ordinary application code.
    assert _ruling("src/assets/logo.svg") is ChangeVerdict.ALLOWED


def test_a_binary_replacement_is_refused_because_nobody_can_read_it() -> None:
    assert _ruling("src/assets/icon.png", binary=True) is ChangeVerdict.REFUSED


@pytest.mark.parametrize(
    "path",
    ["pnpm-lock.yaml", "package.json", "uv.lock", "Cargo.toml", "vite.config.ts", "tsconfig.json"],
)
def test_dependency_and_build_changes_are_separately_reviewed_not_refused(path: str) -> None:
    """Sometimes the repair really is a library upgrade.

    So these are allowed -- but never as a routine accessibility edit, because they change what the
    candidate is built from and a reviewer told "fixed the label" would not know.
    """
    assert _ruling(path) is ChangeVerdict.SEPARATELY_REVIEWED


def test_ordinary_application_code_is_allowed() -> None:
    assert _ruling("src/components/EmailField.tsx") is ChangeVerdict.ALLOWED


def test_a_declared_repair_surface_refuses_paths_outside_it() -> None:
    """An agent reaching outside the surface somebody authorized.

    Without this the only limit is the protected list, and "it was only a small file in another
    service" is how that gets waved through.
    """
    inspection = inspect_patch(
        (
            ProposedChange(path="web/src/Form.tsx", content="x"),
            ProposedChange(path="billing/src/Charge.tsx", content="x"),
        ),
        application_paths=("web/src",),
    )
    verdicts = {r.path: r.verdict for r in inspection.rulings}
    assert verdicts["web/src/Form.tsx"] is ChangeVerdict.ALLOWED
    assert verdicts["billing/src/Charge.tsx"] is ChangeVerdict.REFUSED
    assert not inspection.acceptable


def test_a_declared_surface_cannot_make_its_own_tests_repairable() -> None:
    """Protection beats the allowlist.

    A project declaring `tests/` as its repair surface would otherwise have opted out of the one
    rule that makes a verified repair mean anything.
    """
    inspection = inspect_patch(
        (ProposedChange(path="tests/test_checkout.py", content="assert True"),),
        application_paths=("tests",),
    )
    assert inspection.refusals
    assert "protected tests" in inspection.refusals[0].reason


def test_a_prefix_match_does_not_leak_into_a_sibling_directory() -> None:
    """`web` must not authorize `website`.

    A prefix compared with a bare `startswith` would, and the two are different directories that a
    reviewer reading "the repair surface is web" would never conflate.
    """
    inspection = inspect_patch(
        (ProposedChange(path="website/secrets.ts", content="x"),), application_paths=("web",)
    )
    assert inspection.refusals


def test_an_empty_patch_is_not_acceptable() -> None:
    """Nothing to review is not the same as nothing wrong with it."""
    inspection = inspect_patch(())
    assert not inspection.acceptable
    assert "changes nothing" in inspection.explain()


def test_the_explanation_names_every_refusal_and_counts_the_rest() -> None:
    """A refusal a caller cannot act on becomes a caller trying variations until one passes."""
    inspection = inspect_patch(
        (
            ProposedChange(path="src/Form.tsx", content="x"),
            ProposedChange(path="tests/test_form.py", content="x"),
            ProposedChange(path="pnpm-lock.yaml", content="x"),
        )
    )
    explanation = inspection.explain()
    assert "tests/test_form.py" in explanation
    assert "protected tests" in explanation
    assert "pnpm-lock.yaml" in explanation
    assert "1 application path(s) allowed" in explanation
    assert not inspection.acceptable
