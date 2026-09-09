"""Approval and execution-grant authority.

Requirements: FR-001, FR-010, FR-014. Invariants: INV-05, INV-08, INV-16.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from accessforge_domain.authority import (
    Approval,
    AuthorityError,
    ChildAuthorization,
    ExecutionGrant,
    authorizes_merge_or_deploy,
    authorizes_patch,
    check_child_at_dispatch,
    mint_child_authorization,
)
from accessforge_domain.states import ApprovalScope

NOW = "2026-09-09T12:00:00Z"
LATER = "2026-09-09T18:00:00Z"

APPROVAL = Approval(
    approval_id="ap1",
    scope=ApprovalScope.RUN_EFFECTS,
    actor_id="user-1",
    workspace_id="ws-1",
    target_id="run-1",
    target_digest="a" * 64,
    expected_revision=3,
    expires_at=LATER,
)

VALID_CHECK = {
    "now": NOW,
    "scope": ApprovalScope.RUN_EFFECTS,
    "workspace_id": "ws-1",
    "target_id": "run-1",
    "target_digest": "a" * 64,
    "current_revision": 3,
}

GRANT = ExecutionGrant(
    grant_id="g1",
    workspace_id="ws-1",
    project_id="p1",
    environment="staging",
    allowed_journey_version_ids=frozenset({"jv1", "jv2"}),
    allowed_policy_version_ids=frozenset({"pv1"}),
    permitted_effects=frozenset({"FIXTURE_SUBMIT", "FIXTURE_RESET"}),
    action_budget=200,
    wall_time_budget_seconds=900,
    expires_at=LATER,
    revision=7,
)

MINT = {
    "now": NOW,
    "authorization_id": "auth-1",
    "run_id": "run-1",
    "journey_version_id": "jv1",
    "policy_version_id": "pv1",
    "permitted_effects": frozenset({"FIXTURE_SUBMIT"}),
    "action_budget": 50,
    "wall_time_budget_seconds": 300,
    "expires_at": "2026-09-09T13:00:00Z",
    "issuing_service_identity": "dispatcher@accessforge",
}


# --- approvals -------------------------------------------------------------------------------


def test_a_matching_approval_is_accepted() -> None:
    # Allowed-path control: an always-deny implementation must fail this.
    APPROVAL.check(**VALID_CHECK)


def test_a_revoked_approval_is_refused() -> None:
    with pytest.raises(AuthorityError, match="revoked"):
        replace(APPROVAL, revoked=True).check(**VALID_CHECK)


def test_an_expired_approval_is_refused() -> None:
    with pytest.raises(AuthorityError, match="expired"):
        APPROVAL.check(**{**VALID_CHECK, "now": "2026-09-10T00:00:00Z"})


def test_expiry_is_exclusive_at_the_boundary() -> None:
    with pytest.raises(AuthorityError, match="expired"):
        APPROVAL.check(**{**VALID_CHECK, "now": LATER})


@pytest.mark.parametrize("scope", [ApprovalScope.PATCH_APPLY, ApprovalScope.GITHUB_PUBLISH])
def test_scopes_do_not_imply_one_another(scope: ApprovalScope) -> None:
    """A RUN_EFFECTS approval authorizes runs and nothing else."""
    with pytest.raises(AuthorityError, match="do not imply"):
        APPROVAL.check(**{**VALID_CHECK, "scope": scope})


def test_a_changed_target_digest_invalidates_the_approval() -> None:
    """INV-08: approval binds an exact input, not a moving one."""
    with pytest.raises(AuthorityError, match="digest has changed"):
        APPROVAL.check(**{**VALID_CHECK, "target_digest": "b" * 64})


def test_a_stale_expected_revision_invalidates_the_approval() -> None:
    with pytest.raises(AuthorityError, match="revision"):
        APPROVAL.check(**{**VALID_CHECK, "current_revision": 4})


def test_an_approval_from_another_workspace_is_refused() -> None:
    """INV-07: a forged or substituted workspace is not an authorization."""
    with pytest.raises(AuthorityError, match="different workspace"):
        APPROVAL.check(**{**VALID_CHECK, "workspace_id": "ws-2"})


def test_an_approval_for_another_target_is_refused() -> None:
    with pytest.raises(AuthorityError, match="different target"):
        APPROVAL.check(**{**VALID_CHECK, "target_id": "run-2"})


# --- execution grants ------------------------------------------------------------------------


def test_a_child_can_be_minted_within_the_grant() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    assert child.scope is ApprovalScope.RUN_EFFECTS
    assert child.parent_grant_id == "g1"
    assert child.parent_grant_revision == 7
    assert child.issuing_service_identity == "dispatcher@accessforge"


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("journey_version_id", "jv-unknown", "journey version"),
        ("policy_version_id", "pv-unknown", "policy version"),
        ("permitted_effects", frozenset({"FIXTURE_SUBMIT", "SEND_EMAIL"}), "broaden"),
        ("action_budget", 500, "action budget"),
        ("wall_time_budget_seconds", 5000, "wall-time budget"),
        ("expires_at", "2026-09-10T00:00:00Z", "outlive"),
    ],
)
def test_no_schedule_may_broaden_its_grant(field_name: str, value: object, message: str) -> None:
    with pytest.raises(AuthorityError, match=message):
        mint_child_authorization(GRANT, **{**MINT, field_name: value})


def test_minting_requires_a_usable_grant() -> None:
    with pytest.raises(AuthorityError, match="revoked"):
        mint_child_authorization(replace(GRANT, revoked=True), **MINT)
    with pytest.raises(AuthorityError, match="expired"):
        mint_child_authorization(GRANT, **{**MINT, "now": "2026-09-10T00:00:00Z"})


def test_the_issuing_service_identity_must_be_recorded() -> None:
    with pytest.raises(AuthorityError, match="issuing service identity"):
        mint_child_authorization(GRANT, **{**MINT, "issuing_service_identity": "  "})


# --- recheck at dispatch ---------------------------------------------------------------------


def _dispatch_args() -> dict[str, object]:
    return {"now": NOW, "workspace_id": "ws-1", "run_id": "run-1"}


def test_a_freshly_minted_child_passes_the_dispatch_recheck() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    check_child_at_dispatch(child, GRANT, **_dispatch_args())  # type: ignore[arg-type]


def test_a_grant_revised_after_minting_invalidates_its_children() -> None:
    """The decision the child represents was made against revision 7.

    If the grant has since changed, nobody has authorized what is about to happen.
    """
    child = mint_child_authorization(GRANT, **MINT)
    with pytest.raises(AuthorityError, match="changed since"):
        check_child_at_dispatch(child, replace(GRANT, revision=8), **_dispatch_args())  # type: ignore[arg-type]


def test_a_grant_revoked_after_minting_invalidates_its_children() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    with pytest.raises(AuthorityError, match="revoked"):
        check_child_at_dispatch(child, replace(GRANT, revoked=True), **_dispatch_args())  # type: ignore[arg-type]


def test_an_expired_child_is_refused_at_dispatch() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    with pytest.raises(AuthorityError, match="expired"):
        check_child_at_dispatch(
            child, GRANT, now="2026-09-09T14:00:00Z", workspace_id="ws-1", run_id="run-1"
        )


def test_a_child_cannot_be_dispatched_for_a_different_run_or_workspace() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    with pytest.raises(AuthorityError, match="different run"):
        check_child_at_dispatch(child, GRANT, now=NOW, workspace_id="ws-1", run_id="run-9")
    with pytest.raises(AuthorityError, match="different workspace"):
        check_child_at_dispatch(child, GRANT, now=NOW, workspace_id="ws-9", run_id="run-1")


def test_a_child_cannot_be_checked_against_an_unrelated_grant() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    with pytest.raises(AuthorityError, match="does not belong"):
        check_child_at_dispatch(child, replace(GRANT, grant_id="g2"), **_dispatch_args())  # type: ignore[arg-type]


def test_a_forged_child_claiming_a_wider_parent_revision_is_caught() -> None:
    """A child is not trusted just because it says it came from somewhere."""
    forged = ChildAuthorization(
        authorization_id="auth-forged",
        run_id="run-1",
        workspace_id="ws-1",
        project_id="p1",
        journey_version_id="jv1",
        policy_version_id="pv1",
        permitted_effects=frozenset({"SEND_EMAIL"}),
        action_budget=99999,
        wall_time_budget_seconds=99999,
        expires_at=LATER,
        parent_grant_id="g1",
        parent_grant_revision=99,  # a revision the grant has never had
        issuing_service_identity="attacker",
    )
    with pytest.raises(AuthorityError, match="changed since"):
        check_child_at_dispatch(forged, GRANT, **_dispatch_args())  # type: ignore[arg-type]


# --- what grants never authorize --------------------------------------------------------------


def test_a_run_grant_never_authorizes_a_patch_or_a_publication() -> None:
    child = mint_child_authorization(GRANT, **MINT)
    assert child.scope is ApprovalScope.RUN_EFFECTS
    assert not authorizes_patch(child.scope)


def test_patch_apply_authorizes_a_candidate_but_never_a_merge_or_deploy() -> None:
    """INV-16 and CONTRACTS section 4: an isolated candidate workspace only."""
    assert authorizes_patch(ApprovalScope.PATCH_APPLY)
    assert not authorizes_merge_or_deploy(ApprovalScope.PATCH_APPLY)


@pytest.mark.parametrize("scope", list(ApprovalScope))
def test_no_scope_whatsoever_authorizes_a_merge_or_deployment(scope: ApprovalScope) -> None:
    assert not authorizes_merge_or_deploy(scope)


# --- timestamp comparison (independent review finding 2) -------------------------------------


def test_an_expired_approval_is_refused_across_fractional_second_precision() -> None:
    """Regression: lexicographic comparison failed open here.

    "2026-09-09T12:00:00.000001Z" is one microsecond AFTER "2026-09-09T12:00:00Z", but as text it
    sorts BEFORE it, because "." (0x2E) is lower than "Z" (0x5A). Both are valid under the
    rfc3339Utc schema pattern, so this was reachable from ordinary inputs — Python emits the
    fractional part only when microseconds are non-zero.
    """
    with pytest.raises(AuthorityError, match="expired"):
        APPROVAL.check(**{**VALID_CHECK, "now": "2026-09-09T18:00:00.000001Z"})


@pytest.mark.parametrize(
    ("now", "expired"),
    [
        ("2026-09-09T17:59:59.999999Z", False),
        ("2026-09-09T18:00:00Z", True),
        ("2026-09-09T18:00:00.000000Z", True),
        ("2026-09-09T18:00:00.000001Z", True),
        ("2026-09-09T18:00:01Z", True),
    ],
)
def test_expiry_boundary_is_precision_independent(now: str, expired: bool) -> None:
    if expired:
        with pytest.raises(AuthorityError, match="expired"):
            APPROVAL.check(**{**VALID_CHECK, "now": now})
    else:
        APPROVAL.check(**{**VALID_CHECK, "now": now})  # allowed-path control


def test_a_child_cannot_outlive_its_parent_by_a_fraction_of_a_second() -> None:
    with pytest.raises(AuthorityError, match="outlive"):
        mint_child_authorization(GRANT, **{**MINT, "expires_at": "2026-09-09T18:00:00.000001Z"})


def test_a_child_expiring_exactly_with_its_parent_is_allowed() -> None:
    child = mint_child_authorization(GRANT, **{**MINT, "expires_at": LATER})
    assert child.expires_at == LATER


@pytest.mark.parametrize(
    "bad",
    [
        "2026-09-09T12:00:00+05:30",  # local offset reintroduces the comparison hazard
        "2026-09-09 12:00:00Z",  # space instead of T
        "2026-09-09T12:00:00",  # naive
        "not-a-timestamp",
        "",
    ],
)
def test_malformed_expiry_is_refused_at_construction(bad: str) -> None:
    """The domain package defends its own invariant rather than trusting upstream validation."""
    from accessforge_domain.timestamps import TimestampError

    with pytest.raises(TimestampError):
        replace(APPROVAL, expires_at=bad)


def test_a_forged_child_with_the_correct_parent_revision_is_still_refused() -> None:
    """Regression: containment was only checked at minting, not at dispatch.

    ChildAuthorization is a plain dataclass and can be constructed directly, so a child that never
    passed through mint_child_authorization could present itself at dispatch. Carrying the correct
    current parent revision was enough to be accepted, authorizing effects and budgets the grant
    never permitted.
    """
    forged = ChildAuthorization(
        authorization_id="auth-forged",
        run_id="run-1",
        workspace_id="ws-1",
        project_id="p1",
        journey_version_id="jv-not-allowed",
        policy_version_id="pv-not-allowed",
        permitted_effects=frozenset({"SEND_EMAIL"}),
        action_budget=999999,
        wall_time_budget_seconds=999999,
        expires_at=LATER,
        parent_grant_id="g1",
        parent_grant_revision=7,  # the grant's actual current revision
        issuing_service_identity="attacker",
    )
    with pytest.raises(AuthorityError):
        check_child_at_dispatch(forged, GRANT, **_dispatch_args())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("journey_version_id", "jv-not-allowed"),
        ("policy_version_id", "pv-not-allowed"),
        ("permitted_effects", frozenset({"SEND_EMAIL"})),
        ("action_budget", 999999),
        ("wall_time_budget_seconds", 999999),
    ],
)
def test_every_containment_axis_is_rechecked_at_dispatch(field_name: str, value: object) -> None:
    """Minting is not enough: each axis must be verified again when the child is used."""
    legitimate = mint_child_authorization(GRANT, **MINT)
    tampered = replace(legitimate, **{field_name: value})
    with pytest.raises(AuthorityError):
        check_child_at_dispatch(tampered, GRANT, **_dispatch_args())  # type: ignore[arg-type]
