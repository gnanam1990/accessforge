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


def test_owner_invitation_http_readback_and_denials(
    test_database_url: str, invited: tuple[str, str, str, int, str]
) -> None:
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.auth.sessions import CSRF_HEADER, SESSION_COOKIE, issue_session
    from accessforge_api.config import ApiSettings

    workspace, owner, target, subject, _ = invited
    with workspace_connection(test_database_url, workspace) as conn:
        owner_session = issue_session(conn, user_id=owner)
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,'VIEWER')",
            (workspace, target),
        )
        viewer_session = issue_session(conn, user_id=target)
    settings = ApiSettings(
        database_url=test_database_url,
        evidence_endpoint_url="http://127.0.0.1:9000",
        evidence_bucket="unused",
        evidence_access_key="unused",
        evidence_secret_key="unused",
        environment="test",
    )
    base = f"/v1/workspaces/{workspace}/membership-invitations"
    path = f"{base}/{uuid4()}"
    offer = {
        "githubSubject": str(subject),
        "role": "REVIEWER",
        "ttlSeconds": 600,
        "reason": "Explicit test offer",
    }
    with TestClient(create_app(settings)) as client:
        client.cookies.set(SESSION_COOKIE, owner_session.session_token)
        assert client.put(path, json=offer, headers={"If-Match": '"0"'}).status_code == 403
        headers = {CSRF_HEADER: owner_session.csrf_token}
        assert client.put(path, json=offer, headers=headers).json()["code"] == "IF_MATCH_REQUIRED"
        headers["If-Match"] = '"0"'
        created = client.put(path, json=offer, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["githubSubject"] == str(subject)
        assert created.json()["state"] == "PENDING"
        assert created.headers["etag"] == '"1"'
        assert created.headers["cache-control"] == "no-store"
        assert client.put(path, json=offer, headers=headers).json()["code"] == "STALE_REVISION"
        assert client.get(path).json() == created.json()
        page = client.get(base, params={"limit": 1}).json()
        assert len(page["items"]) == 1 and page["nextCursor"] is not None
        following = client.get(base, params={"limit": 1, "after": page["nextCursor"]}).json()
        assert len(following["items"]) == 1
        assert following["items"][0]["invitationId"] != page["items"][0]["invitationId"]
        headers["If-Match"] = '"1"'
        revoked = client.delete(path, headers=headers)
        assert revoked.status_code == 200 and revoked.json()["state"] == "REVOKED"
        assert client.get(path).json()["revision"] == 2
        client.cookies.set(SESSION_COOKIE, viewer_session.session_token)
        assert client.get(base).status_code == 403
        assert client.get(path).status_code == 403
        assert (
            client.delete(
                path, headers={CSRF_HEADER: viewer_session.csrf_token, "If-Match": '"2"'}
            ).status_code
            == 403
        )
    with workspace_connection(test_database_url, workspace) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE workspace_id=%s "
            "AND action='MEMBERSHIP_INVITATION_ADMINISTER' AND outcome='DENIED'",
            (workspace,),
        ).fetchone()
        assert row and row["n"] == 3


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
