"""Approvals and execution grants.

Authority in AccessForge is exact and non-transitive. Nothing here widens: a scope authorizes one
kind of act on one target at one digest, and a grant that produces child authorizations can never
mint a child broader than itself.

Two rules carry most of the weight (CONTRACTS section 4, INV-08):

* **Everything is rechecked at dispatch.** An approval that was valid when created may be expired,
  revoked, or bound to a digest that has since changed. Validity is a question about *now*, which
  is why every check takes the current time explicitly rather than reading a clock.
* **Scopes do not imply one another.** RUN_EFFECTS never authorizes a patch. PATCH_APPLY
  authorizes an isolated candidate workspace and never a merge or a deployment. GITHUB_PUBLISH
  binds one exact publication payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .states import ApprovalScope
from .timestamps import is_after, is_expired, parse_rfc3339_utc


class AuthorityError(Exception):
    """Authorization was refused. Never downgraded to a warning."""


@dataclass(frozen=True, slots=True)
class Approval:
    """An exact, single-target authorization."""

    approval_id: str
    scope: ApprovalScope
    actor_id: str
    workspace_id: str
    target_id: str
    target_digest: str
    expected_revision: int
    expires_at: str
    revoked: bool = False

    def __post_init__(self) -> None:
        # Validated here rather than trusted from upstream schema validation: this package is
        # pure domain logic and can be constructed directly.
        parse_rfc3339_utc(self.expires_at, field="Approval.expires_at")

    def check(
        self,
        *,
        now: str,
        scope: ApprovalScope,
        workspace_id: str,
        target_id: str,
        target_digest: str,
        current_revision: int,
    ) -> None:
        """Raise unless this approval authorizes exactly this act, right now.

        Every parameter is compared. An approval for a different workspace, a different target, a
        changed digest, or a moved revision is not a weaker authorization — it is not an
        authorization at all.
        """
        if self.revoked:
            raise AuthorityError(f"approval {self.approval_id} has been revoked")
        # Parsed, never compared as text: a difference in fractional-second precision between
        # two schema-valid timestamps would otherwise decide expiry, failing open.
        if is_expired(now=now, expires_at=self.expires_at):
            raise AuthorityError(
                f"approval {self.approval_id} expired at {self.expires_at} (now {now})"
            )
        if self.scope is not scope:
            raise AuthorityError(
                f"approval {self.approval_id} has scope {self.scope}, not {scope}; "
                "scopes do not imply one another"
            )
        if self.workspace_id != workspace_id:
            raise AuthorityError("approval belongs to a different workspace")
        if self.target_id != target_id:
            raise AuthorityError("approval names a different target")
        if self.target_digest != target_digest:
            raise AuthorityError(
                "target digest has changed since approval; a changed input requires a new "
                "authorization"
            )
        if self.expected_revision != current_revision:
            raise AuthorityError(
                f"approval expects revision {self.expected_revision}, target is at "
                f"{current_revision}"
            )


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """A standing authorization for recurring runs.

    Not an exact-run approval. It describes the envelope within which the trusted dispatcher may
    mint exact RUN_EFFECTS child authorizations, and it never authorizes a repair or a
    publication no matter how it is configured.
    """

    grant_id: str
    workspace_id: str
    project_id: str
    environment: str
    allowed_journey_version_ids: frozenset[str]
    allowed_policy_version_ids: frozenset[str]
    permitted_effects: frozenset[str]
    action_budget: int
    wall_time_budget_seconds: int
    expires_at: str
    revision: int
    revoked: bool = False

    def __post_init__(self) -> None:
        parse_rfc3339_utc(self.expires_at, field="ExecutionGrant.expires_at")

    def check_usable(self, *, now: str, expected_revision: int | None = None) -> None:
        if self.revoked:
            raise AuthorityError(f"execution grant {self.grant_id} has been revoked")
        if is_expired(now=now, expires_at=self.expires_at):
            raise AuthorityError(
                f"execution grant {self.grant_id} expired at {self.expires_at} (now {now})"
            )
        if expected_revision is not None and expected_revision != self.revision:
            raise AuthorityError(
                f"execution grant {self.grant_id} is at revision {self.revision}, "
                f"expected {expected_revision}; it changed since this occurrence was planned"
            )


@dataclass(frozen=True, slots=True)
class ChildAuthorization:
    """An exact RUN_EFFECTS authorization minted from a parent grant.

    It records which grant produced it, at which revision, and which service identity issued it,
    so a child can always be traced back and rechecked against its parent.
    """

    authorization_id: str
    run_id: str
    workspace_id: str
    project_id: str
    journey_version_id: str
    policy_version_id: str
    permitted_effects: frozenset[str]
    action_budget: int
    wall_time_budget_seconds: int
    expires_at: str
    parent_grant_id: str
    parent_grant_revision: int
    issuing_service_identity: str
    scope: ApprovalScope = field(default=ApprovalScope.RUN_EFFECTS)

    def __post_init__(self) -> None:
        parse_rfc3339_utc(self.expires_at, field="ChildAuthorization.expires_at")


def mint_child_authorization(
    grant: ExecutionGrant,
    *,
    now: str,
    authorization_id: str,
    run_id: str,
    journey_version_id: str,
    policy_version_id: str,
    permitted_effects: frozenset[str],
    action_budget: int,
    wall_time_budget_seconds: int,
    expires_at: str,
    issuing_service_identity: str,
) -> ChildAuthorization:
    """Mint an exact child authorization, or refuse.

    A child may only ever be narrower than or equal to its parent. Every widening attempt below is
    a refusal rather than a clamp: silently narrowing a request would hide the fact that a
    schedule tried to exceed its grant.
    """
    grant.check_usable(now=now)

    if journey_version_id not in grant.allowed_journey_version_ids:
        raise AuthorityError(
            f"journey version {journey_version_id} is not permitted by grant {grant.grant_id}"
        )
    if policy_version_id not in grant.allowed_policy_version_ids:
        raise AuthorityError(
            f"policy version {policy_version_id} is not permitted by grant {grant.grant_id}"
        )
    if not permitted_effects <= grant.permitted_effects:
        extra = sorted(permitted_effects - grant.permitted_effects)
        raise AuthorityError(
            f"child would add effects not in the grant: {extra}; no schedule may broaden its grant"
        )
    if action_budget > grant.action_budget:
        raise AuthorityError(
            f"child action budget {action_budget} exceeds the grant's {grant.action_budget}"
        )
    if wall_time_budget_seconds > grant.wall_time_budget_seconds:
        raise AuthorityError(
            f"child wall-time budget {wall_time_budget_seconds} exceeds the grant's "
            f"{grant.wall_time_budget_seconds}"
        )
    if is_after(later=expires_at, earlier=grant.expires_at):
        raise AuthorityError(
            "child would outlive its parent grant; a child cannot extend the grant's expiry"
        )
    if not issuing_service_identity.strip():
        raise AuthorityError("the issuing service identity must be recorded on every child")

    return ChildAuthorization(
        authorization_id=authorization_id,
        run_id=run_id,
        workspace_id=grant.workspace_id,
        project_id=grant.project_id,
        journey_version_id=journey_version_id,
        policy_version_id=policy_version_id,
        permitted_effects=permitted_effects,
        action_budget=action_budget,
        wall_time_budget_seconds=wall_time_budget_seconds,
        expires_at=expires_at,
        parent_grant_id=grant.grant_id,
        parent_grant_revision=grant.revision,
        issuing_service_identity=issuing_service_identity,
    )


def check_child_at_dispatch(
    child: ChildAuthorization,
    parent: ExecutionGrant,
    *,
    now: str,
    workspace_id: str,
    run_id: str,
) -> None:
    """Recheck a child authorization at the moment of dispatch.

    Minting is not enough. Between minting and dispatch the parent grant may have been revoked or
    revised, and a child whose parent has moved underneath it no longer represents a decision
    anyone made.
    """
    if child.parent_grant_id != parent.grant_id:
        raise AuthorityError("child authorization does not belong to this grant")
    if is_expired(now=now, expires_at=child.expires_at):
        raise AuthorityError(f"child authorization expired at {child.expires_at} (now {now})")
    if child.workspace_id != workspace_id:
        raise AuthorityError("child authorization belongs to a different workspace")
    if child.run_id != run_id:
        raise AuthorityError("child authorization is bound to a different run")

    # The parent must still be usable *and* unchanged since minting.
    parent.check_usable(now=now, expected_revision=child.parent_grant_revision)


def authorizes_patch(scope: ApprovalScope) -> bool:
    """Whether a scope permits applying a patch to an isolated candidate workspace."""
    return scope is ApprovalScope.PATCH_APPLY


def authorizes_merge_or_deploy(scope: ApprovalScope) -> bool:
    """No scope in this product authorizes a merge or a deployment.

    Stated as a function rather than left implicit so that any future caller asking the question
    gets a definite answer instead of inventing one. PATCH_APPLY in particular authorizes an
    isolated candidate only.
    """
    return False
