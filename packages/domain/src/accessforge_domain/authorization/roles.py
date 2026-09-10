"""Workspace roles and the permission matrix.

Roles come from SECURITY-PRIVACY section 3: owners manage membership, scope, retention and
entitlements; maintainers configure authorized projects and request approved work; reviewers read
authorized evidence and record review, and explicitly do **not** gain execution or publication
authority by reviewing; viewers are read-only.

The matrix is data, not scattered `if` statements, so it can be enumerated, printed in a handoff,
and tested exhaustively. Anything not listed is denied — there is no implicit inheritance between
roles and no "admin" shortcut.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Workspace membership role. A principal holds exactly one per workspace."""

    OWNER = "OWNER"
    MAINTAINER = "MAINTAINER"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"


class Permission(StrEnum):
    """A single authorizable capability.

    Deliberately granular along the boundaries module 03 requires to be separable: membership
    administration, project configuration, execution approval, patch review, evidence
    reading/export and infrastructure operations.
    """

    MEMBERSHIP_ADMINISTER = "MEMBERSHIP_ADMINISTER"
    """Add, remove or re-role members. Owner only."""

    WORKSPACE_CONFIGURE = "WORKSPACE_CONFIGURE"
    """Retention, entitlements and workspace-wide settings. Owner only."""

    PROJECT_CONFIGURE = "PROJECT_CONFIGURE"
    """Authorize projects, environments and permitted effects."""

    RUN_REQUEST = "RUN_REQUEST"
    """Ask for a run. Requesting is not approving."""

    RUN_APPROVE = "RUN_APPROVE"
    """Issue a RUN_EFFECTS approval for an exact frozen task."""

    PATCH_APPROVE = "PATCH_APPROVE"
    """Issue a PATCH_APPLY approval for an isolated candidate workspace. Never a merge or deploy."""

    PATCH_REVIEW = "PATCH_REVIEW"
    """Record a human review verdict. Reviewing confers no execution authority."""

    EVIDENCE_READ = "EVIDENCE_READ"
    """Read run evidence within the workspace."""

    EVIDENCE_EXPORT = "EVIDENCE_EXPORT"
    """Produce a redacted export. Separate from reading: an export leaves the system."""

    GITHUB_PUBLISH_APPROVE = "GITHUB_PUBLISH_APPROVE"
    """Issue a GITHUB_PUBLISH approval for one exact payload.

    Separate from attaching a repository: read-only preparation does not authorize publication.
    """

    INFRASTRUCTURE_OPERATE = "INFRASTRUCTURE_OPERATE"
    """Enroll runners, manage leases, quarantine desktops."""


# The authoritative matrix. Listed explicitly per role rather than derived by inheritance, because
# "maintainer gets everything a reviewer gets plus extras" is how a reviewer quietly acquires
# execution authority when someone adds a permission to the wrong tier.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.MAINTAINER: frozenset(
        {
            Permission.PROJECT_CONFIGURE,
            Permission.RUN_REQUEST,
            Permission.RUN_APPROVE,
            Permission.PATCH_APPROVE,
            Permission.EVIDENCE_READ,
            Permission.EVIDENCE_EXPORT,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Permission.EVIDENCE_READ,
            Permission.PATCH_REVIEW,
        }
    ),
    Role.VIEWER: frozenset({Permission.EVIDENCE_READ}),
}


class AuthorizationError(Exception):
    """A permission check failed. Always an error, never a warning or a filtered result."""


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def role_permits(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def assert_permitted(role: Role, permission: Permission) -> None:
    if not role_permits(role, permission):
        raise AuthorizationError(
            f"role {role} does not hold {permission}; no role inherits another's permissions"
        )
