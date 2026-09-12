"""The role and service-identity authorization matrix, tested exhaustively.

Every role is checked against every permission, and every service identity against every event
kind — not a sample. The interesting failures in an authorization matrix are the cells nobody
thought to write a test for.

Requirements: FR-001, FR-014. Invariants: INV-01, INV-05, INV-07, INV-08.
"""

from __future__ import annotations

import itertools

import pytest

from accessforge_domain.authorization import (
    EVENT_PRODUCER_ACL,
    ROLE_PERMISSIONS,
    AuthorizationError,
    EventSubmissionError,
    HumanPrincipal,
    MachinePrincipal,
    Permission,
    Role,
    ServiceIdentity,
    assert_may_submit_event,
    assert_permitted,
    may_submit_event,
    permissions_for,
    role_permits,
)
from accessforge_domain.authorization.events import assert_may_dispatch_os_action

# The expected matrix, written out independently of the implementation. If this duplicates
# ROLE_PERMISSIONS, that is the point: a change to the matrix must be made deliberately in two
# places, one of which is a test that a reviewer reads.
EXPECTED: dict[Role, set[Permission]] = {
    Role.OWNER: set(Permission),
    Role.MAINTAINER: {
        Permission.PROJECT_CONFIGURE,
        Permission.RUN_REQUEST,
        Permission.RUN_APPROVE,
        Permission.PATCH_APPROVE,
        Permission.EVIDENCE_READ,
        Permission.EVIDENCE_EXPORT,
    },
    Role.REVIEWER: {Permission.EVIDENCE_READ, Permission.PATCH_REVIEW},
    Role.VIEWER: {Permission.EVIDENCE_READ},
}


@pytest.mark.parametrize(("role", "permission"), list(itertools.product(Role, Permission)))
def test_every_cell_of_the_matrix(role: Role, permission: Permission) -> None:
    expected = permission in EXPECTED[role]
    assert role_permits(role, permission) is expected, f"{role} / {permission}"


@pytest.mark.parametrize("role", list(Role))
def test_assert_permitted_agrees_with_role_permits(role: Role) -> None:
    for permission in Permission:
        if role_permits(role, permission):
            assert_permitted(role, permission)  # allowed-path control
        else:
            with pytest.raises(AuthorizationError):
                assert_permitted(role, permission)


# --- the separations that matter ------------------------------------------------------------


def test_reviewing_confers_no_execution_or_publication_authority() -> None:
    """SECURITY-PRIVACY section 3 states this explicitly."""
    reviewer = permissions_for(Role.REVIEWER)
    assert Permission.PATCH_REVIEW in reviewer
    for forbidden in (
        Permission.RUN_APPROVE,
        Permission.PATCH_APPROVE,
        Permission.GITHUB_PUBLISH_APPROVE,
        Permission.RUN_REQUEST,
        Permission.EVIDENCE_EXPORT,
    ):
        assert forbidden not in reviewer


def test_only_the_owner_administers_membership_or_workspace_configuration() -> None:
    for permission in (Permission.MEMBERSHIP_ADMINISTER, Permission.WORKSPACE_CONFIGURE):
        holders = {role for role in Role if role_permits(role, permission)}
        assert holders == {Role.OWNER}, f"{permission} held by {holders}"


def test_publication_and_infrastructure_are_owner_only() -> None:
    """Publication is a separate permission from attaching a repository (FR-018), and it is not
    something a project maintainer acquires by configuring a project."""
    for permission in (
        Permission.GITHUB_PUBLISH_APPROVE,
        Permission.INFRASTRUCTURE_OPERATE,
    ):
        assert {role for role in Role if role_permits(role, permission)} == {Role.OWNER}


def test_requesting_a_run_is_not_approving_one() -> None:
    # A viewer can do neither; the two permissions are distinct capabilities, not a scale.
    assert not role_permits(Role.VIEWER, Permission.RUN_REQUEST)
    assert not role_permits(Role.VIEWER, Permission.RUN_APPROVE)
    assert len({Permission.RUN_REQUEST, Permission.RUN_APPROVE}) == 2


def test_exporting_is_separate_from_reading() -> None:
    """An export leaves the system, so it is not implied by read access."""
    assert role_permits(Role.VIEWER, Permission.EVIDENCE_READ)
    assert not role_permits(Role.VIEWER, Permission.EVIDENCE_EXPORT)
    assert role_permits(Role.REVIEWER, Permission.EVIDENCE_READ)
    assert not role_permits(Role.REVIEWER, Permission.EVIDENCE_EXPORT)


def test_no_role_inherits_another_roles_permissions() -> None:
    """Roles are not a hierarchy.

    Maintainer is not a superset of reviewer: a maintainer cannot record a review verdict, because
    approving work and independently reviewing it are different jobs.
    """
    maintainer = permissions_for(Role.MAINTAINER)
    reviewer = permissions_for(Role.REVIEWER)
    assert not reviewer <= maintainer, "reviewer is not a subset of maintainer"
    assert Permission.PATCH_REVIEW not in maintainer


def test_the_permission_vocabulary_has_not_silently_grown() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)
    assert {p.value for p in Permission} == {
        "MEMBERSHIP_ADMINISTER",
        "WORKSPACE_CONFIGURE",
        "PROJECT_CONFIGURE",
        "RUN_REQUEST",
        "RUN_APPROVE",
        "PATCH_APPROVE",
        "PATCH_REVIEW",
        "EVIDENCE_READ",
        "EVIDENCE_EXPORT",
        "GITHUB_PUBLISH_APPROVE",
        "INFRASTRUCTURE_OPERATE",
    }


# --- machine principals hold no role permissions ---------------------------------------------


@pytest.mark.parametrize(
    ("service", "permission"), list(itertools.product(ServiceIdentity, Permission))
)
def test_no_service_identity_holds_any_workspace_permission(
    service: ServiceIdentity, permission: Permission
) -> None:
    """FR-014: service credentials do not inherit administrator rights by convenience."""
    principal = MachinePrincipal(
        service_identity=service, workspace_id="ws-1", credential_id="cred-1"
    )
    assert principal.permits(permission) is False


def test_a_desktop_lease_credential_cannot_approve_a_patch() -> None:
    lease_holder = MachinePrincipal(
        service_identity=ServiceIdentity.SUPERVISOR,
        workspace_id="ws-1",
        credential_id="cred-1",
        run_id="run-1",
        lease_id="lease-1",
    )
    assert not lease_holder.permits(Permission.PATCH_APPROVE)
    assert not lease_holder.permits(Permission.EVIDENCE_EXPORT)


def test_a_human_principal_carries_its_role() -> None:
    # Allowed-path control for the human side.
    owner = HumanPrincipal(user_id="u1", workspace_id="ws-1", role=Role.OWNER, session_id="s1")
    assert owner.permits(Permission.MEMBERSHIP_ADMINISTER)
    viewer = HumanPrincipal(user_id="u2", workspace_id="ws-1", role=Role.VIEWER, session_id="s2")
    assert not viewer.permits(Permission.MEMBERSHIP_ADMINISTER)


# --- evidence producer ACL -------------------------------------------------------------------

ALL_EVENT_KINDS = sorted(
    {kind for kinds in EVENT_PRODUCER_ACL.values() for kind in kinds}
    | {
        "RUN_STARTED",
        "PREFLIGHT_RESULT",
        "ACTION_INTENT",
        "ACTION_RESULT",
        "READER_OBSERVATION",
        "ASSERTION_OBSERVATION",
        "EFFECT_RECEIPT",
        "BUDGET_EVENT",
        "INTERRUPTION",
        "RUN_FINISHED",
    }
)


def _machine(service: ServiceIdentity, **kw: object) -> MachinePrincipal:
    return MachinePrincipal(
        service_identity=service,
        workspace_id="ws-1",
        credential_id="c1",
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("service", "event_type"), list(itertools.product(ServiceIdentity, ALL_EVENT_KINDS))
)
def test_every_cell_of_the_producer_acl(service: ServiceIdentity, event_type: str) -> None:
    expected = event_type in EVENT_PRODUCER_ACL[service]
    assert may_submit_event(_machine(service), event_type) is expected


def test_a_supervisor_cannot_forge_an_application_receipt() -> None:
    """The separation the whole evidence model rests on.

    The supervisor is driving the journey. If it could also attest that the application recorded
    the task, a run could certify its own success.
    """
    with pytest.raises(EventSubmissionError, match="EFFECT_RECEIPT"):
        assert_may_submit_event(_machine(ServiceIdentity.SUPERVISOR), "EFFECT_RECEIPT")


def test_a_supervisor_cannot_forge_an_observer_assertion() -> None:
    with pytest.raises(EventSubmissionError, match="ASSERTION_OBSERVATION"):
        assert_may_submit_event(_machine(ServiceIdentity.SUPERVISOR), "ASSERTION_OBSERVATION")


def test_an_observer_cannot_submit_reader_or_action_records() -> None:
    for kind in ("READER_OBSERVATION", "ACTION_INTENT", "ACTION_RESULT"):
        with pytest.raises(EventSubmissionError):
            assert_may_submit_event(_machine(ServiceIdentity.OBSERVER), kind)


def test_an_observer_cannot_dispatch_operating_system_actions() -> None:
    """It verifies the application; it must not be able to influence what it is verifying."""
    with pytest.raises(EventSubmissionError, match="may not dispatch"):
        assert_may_dispatch_os_action(_machine(ServiceIdentity.OBSERVER, lease_id="l1"))


def test_a_supervisor_needs_a_lease_to_dispatch_an_action() -> None:
    assert_may_dispatch_os_action(
        _machine(ServiceIdentity.SUPERVISOR, lease_id="l1", run_id="r1")
    )  # allowed-path control
    with pytest.raises(EventSubmissionError, match="without a desktop lease"):
        assert_may_dispatch_os_action(_machine(ServiceIdentity.SUPERVISOR))


def test_the_navigator_submits_no_evidence_at_all() -> None:
    assert EVENT_PRODUCER_ACL[ServiceIdentity.NAVIGATOR] == frozenset()
    for kind in ALL_EVENT_KINDS:
        with pytest.raises(EventSubmissionError):
            assert_may_submit_event(_machine(ServiceIdentity.NAVIGATOR), kind)


def test_ingestion_sequences_but_does_not_author() -> None:
    """If the sequencer could author records, the canonical chain would attest only to itself."""
    assert EVENT_PRODUCER_ACL[ServiceIdentity.INGESTION] == frozenset()
    for kind in ALL_EVENT_KINDS:
        assert not may_submit_event(_machine(ServiceIdentity.INGESTION), kind)


def test_supervisor_and_observer_kinds_never_overlap() -> None:
    supervisor = EVENT_PRODUCER_ACL[ServiceIdentity.SUPERVISOR]
    observer = EVENT_PRODUCER_ACL[ServiceIdentity.OBSERVER]
    assert supervisor and observer  # both non-empty, so the intersection test is meaningful
    assert not (supervisor & observer)


def test_every_contract_event_kind_has_exactly_one_permitted_producer() -> None:
    """No event kind may be producible by two identities, or by none.

    A kind with no producer is dead contract surface; a kind with two has no separation.
    """
    for kind in ALL_EVENT_KINDS:
        producers = [s for s in ServiceIdentity if kind in EVENT_PRODUCER_ACL[s]]
        assert len(producers) == 1, f"{kind} has producers {producers}"
