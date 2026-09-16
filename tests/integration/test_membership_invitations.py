"""Real database invitation boundaries; no provider calls or live access changes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from accessforge_persistence import migrate, unscoped_connection, workspace_connection
from accessforge_persistence.invitations import (
    InvitationError,
    accept_invitation,
    create_invitation,
    revoke_invitation,
)
from accessforge_persistence.memberships import MembershipChangeError

pytestmark = pytest.mark.integration


@pytest.fixture
def invited(test_database_url: str) -> tuple[str, str, str, int, str]:
    migrate(test_database_url)
    workspace, owner, target, invitation = (str(uuid4()) for _ in range(4))
    subject = uuid4().int % (2**62) + 1
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES (%s,'Invitations')", (workspace,))
        for user in (owner, target):
            conn.execute(
                "INSERT INTO app_user(id,email) VALUES (%s,%s)", (user, f"{user}@example.test")
            )
        conn.execute(
            "INSERT INTO github_user_identity(github_subject,user_id) VALUES (%s,%s)",
            (subject, target),
        )
    with workspace_connection(test_database_url, workspace) as conn:
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,'OWNER')",
            (workspace, owner),
        )
        create_invitation(
            conn,
            workspace_id=workspace,
            actor_user_id=owner,
            invitation_id=invitation,
            github_subject=subject,
            role="REVIEWER",
            ttl_seconds=600,
            reason="Review this workspace",
        )
        assert (
            conn.execute(
                "SELECT user_id FROM workspace_membership WHERE user_id=%s", (target,)
            ).fetchone()
            is None
        )
    return workspace, owner, target, subject, invitation


def test_acceptance_is_single_use_and_audited(
    test_database_url: str, invited: tuple[str, str, str, int, str]
) -> None:
    workspace, _, target, subject, invitation = invited
    with workspace_connection(test_database_url, workspace) as conn:
        result = accept_invitation(
            conn,
            workspace_id=workspace,
            invitation_id=invitation,
            authenticated_user_id=target,
            authenticated_github_subject=subject,
            expected_revision=1,
        )
        assert result == {"userId": target, "role": "REVIEWER", "revoked": False, "revision": 1}
        with pytest.raises(InvitationError):
            accept_invitation(
                conn,
                workspace_id=workspace,
                invitation_id=invitation,
                authenticated_user_id=target,
                authenticated_github_subject=subject,
                expected_revision=1,
            )
        receipt = conn.execute(
            "SELECT accepted_by,revision FROM membership_invitation WHERE id=%s", (invitation,)
        ).fetchone()
        assert receipt and str(receipt["accepted_by"]) == target and receipt["revision"] == 2
        audits = conn.execute(
            "SELECT action FROM audit_event WHERE workspace_id=%s", (workspace,)
        ).fetchall()
        assert sorted(row["action"] for row in audits) == [
            "MEMBERSHIP_ADMINISTER",
            "MEMBERSHIP_INVITATION_ACCEPTED",
            "MEMBERSHIP_INVITATION_CREATED",
        ]


@pytest.mark.parametrize(
    "fault",
    [
        "expired",
        "revoked",
        "wrong-subject",
        "wrong-account",
        "binding-revoked",
        "disabled",
        "issuer-demoted",
        "prior-revocation",
    ],
)
def test_refused_acceptance_never_grants_or_consumes(
    test_database_url: str, invited: tuple[str, str, str, int, str], fault: str
) -> None:
    workspace, owner, target, subject, invitation = invited
    if fault in {"disabled", "binding-revoked"}:
        with unscoped_connection(test_database_url) as conn:
            if fault == "disabled":
                conn.execute("UPDATE app_user SET disabled_at=now() WHERE id=%s", (target,))
            else:
                conn.execute(
                    "UPDATE github_user_identity SET revoked_at=now() WHERE user_id=%s", (target,)
                )
    with workspace_connection(test_database_url, workspace) as conn:
        if fault == "expired":
            conn.execute(
                "UPDATE membership_invitation SET created_at=now()-interval '2 hours', "
                "expires_at=now()-interval '1 hour' WHERE id=%s",
                (invitation,),
            )
        if fault == "revoked":
            revoke_invitation(
                conn,
                workspace_id=workspace,
                actor_user_id=owner,
                invitation_id=invitation,
                expected_revision=1,
            )
        if fault == "issuer-demoted":
            conn.execute("UPDATE workspace_membership SET role='VIEWER' WHERE user_id=%s", (owner,))
        if fault == "prior-revocation":
            conn.execute(
                "INSERT INTO workspace_membership(workspace_id,user_id,role,revoked_at) "
                "VALUES (%s,%s,'VIEWER',now())",
                (workspace, target),
            )
        # Catch inside the outer transaction: the service's savepoint must roll back tentative
        # membership/audit writes when expiry is detected after the membership operation.
        with pytest.raises((InvitationError, MembershipChangeError)):
            accept_invitation(
                conn,
                workspace_id=workspace,
                invitation_id=invitation,
                authenticated_user_id=owner if fault == "wrong-account" else target,
                authenticated_github_subject=subject + 1 if fault == "wrong-subject" else subject,
                expected_revision=1,
            )
        assert (
            conn.execute(
                "SELECT user_id FROM workspace_membership WHERE user_id=%s AND revoked_at IS NULL",
                (target,),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT id FROM membership_invitation WHERE id=%s AND accepted_at IS NOT NULL",
                (invitation,),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT id FROM audit_event WHERE action='MEMBERSHIP_ADMINISTER' "
                "AND workspace_id=%s",
                (workspace,),
            ).fetchone()
            is None
        )


def test_invitation_rls_and_concurrent_acceptance(
    test_database_url: str, invited: tuple[str, str, str, int, str]
) -> None:
    workspace, _, target, subject, invitation = invited
    with unscoped_connection(test_database_url) as conn:
        assert conn.execute("SELECT * FROM membership_invitation").fetchall() == []
    with workspace_connection(test_database_url, str(uuid4())) as conn:
        assert conn.execute("SELECT * FROM membership_invitation").fetchall() == []
    barrier = Barrier(2)

    def accept(_: int) -> str:
        barrier.wait(timeout=10)
        try:
            with workspace_connection(test_database_url, workspace) as conn:
                conn.execute("SET LOCAL lock_timeout='10s'")
                accept_invitation(
                    conn,
                    workspace_id=workspace,
                    invitation_id=invitation,
                    authenticated_user_id=target,
                    authenticated_github_subject=subject,
                    expected_revision=1,
                )
            return "accepted"
        except InvitationError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, (1, 2))) == ["accepted", "refused"]
