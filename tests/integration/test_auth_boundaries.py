"""Session, CSRF, membership and enrollment boundaries against a real database.

These are the adversarial cases from module 03: stale membership, expired and reused tokens, forged
roles, a device credential presented as a user session, CSRF, and a body that disagrees with the
route.

Requirements: FR-001, FR-014, FR-020. Invariants: INV-07, INV-08.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from accessforge_api.auth import (
    EnrollmentError,
    MembershipError,
    SessionError,
    create_enrollment,
    device_may_be_admitted,
    issue_session,
    record_audit_event,
    redeem_enrollment,
    require_permission,
    resolve_human_principal,
    resolve_session,
    revoke_all_sessions_for_user,
    revoke_device_admission,
    revoke_device_credential,
    revoke_session,
    rotate_session,
    verify_csrf,
)
from accessforge_api.auth.membership import assert_route_matches_body
from accessforge_domain.authorization import AuthorizationError, Permission, Role
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0x2A))
WS_B = str(uuid.UUID(int=0x2B))
OWNER_ID = str(uuid.UUID(int=0x30))
VIEWER_ID = str(uuid.UUID(int=0x31))
OUTSIDER_ID = str(uuid.UUID(int=0x32))
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    # Checked before anything else: if the role bypasses RLS, every assertion below is
    # meaningless and should say so in one sentence rather than sixteen.
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace, app_user, audit_event RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS_A, "Alpha"), (WS_B, "Beta")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        for uid, email in (
            (OWNER_ID, "owner@example.test"),
            (VIEWER_ID, "viewer@example.test"),
            (OUTSIDER_ID, "outsider@example.test"),
        ):
            conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (uid, email))
    with workspace_connection(test_database_url, WS_A) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS_A, OWNER_ID),
        )
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s,%s,'VIEWER')",
            (WS_A, VIEWER_ID),
        )
    # The outsider is a real user with a real session, and a member of workspace B only.
    with workspace_connection(test_database_url, WS_B) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS_B, OUTSIDER_ID),
        )
    yield test_database_url


# --- sessions ---------------------------------------------------------------------------------


def test_a_fresh_session_resolves(db: str) -> None:
    # Allowed-path control.
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
    with unscoped_connection(db) as conn:
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    assert session.user_id == OWNER_ID


def test_the_raw_session_token_is_never_stored(db: str) -> None:
    """A dump of the session table must not yield a usable credential."""
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
    with unscoped_connection(db) as conn:
        rows = conn.execute("SELECT token_hash, csrf_token_hash FROM user_session").fetchall()
    stored = " ".join(str(v) for row in rows for v in row.values())
    assert issued.session_token not in stored
    assert issued.csrf_token not in stored


def test_an_expired_session_is_refused(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
    later = NOW + timedelta(hours=13)
    with unscoped_connection(db) as conn, pytest.raises(SessionError):
        resolve_session(conn, session_token=issued.session_token, now=later)


def test_a_revoked_session_is_refused_immediately(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        revoke_session(conn, session_id=issued.session_id, now=NOW)
    with unscoped_connection(db) as conn, pytest.raises(SessionError):
        resolve_session(conn, session_token=issued.session_token, now=NOW)


def test_rotation_invalidates_the_previous_token(db: str) -> None:
    """Session fixation defence: the identifier must not survive a privilege change."""
    with unscoped_connection(db) as conn:
        first = issue_session(conn, user_id=OWNER_ID, now=NOW)
        session = resolve_session(conn, session_token=first.session_token, now=NOW)
        second = rotate_session(conn, session=session, now=NOW)

    assert second.session_token != first.session_token
    with unscoped_connection(db) as conn:
        with pytest.raises(SessionError):
            resolve_session(conn, session_token=first.session_token, now=NOW)
        assert (
            resolve_session(conn, session_token=second.session_token, now=NOW).user_id == OWNER_ID
        )

    # The chain is auditable.
    with unscoped_connection(db) as conn:
        row = conn.execute(
            "SELECT rotated_from FROM user_session WHERE id = %s", (second.session_id,)
        ).fetchone()
    assert row is not None and str(row["rotated_from"]) == first.session_id


def test_signing_out_everywhere_revokes_every_live_session(db: str) -> None:
    with unscoped_connection(db) as conn:
        a = issue_session(conn, user_id=OWNER_ID, now=NOW)
        b = issue_session(conn, user_id=OWNER_ID, now=NOW)
        other = issue_session(conn, user_id=VIEWER_ID, now=NOW)
        count = revoke_all_sessions_for_user(conn, user_id=OWNER_ID, now=NOW)
    assert count == 2
    with unscoped_connection(db) as conn:
        for token in (a.session_token, b.session_token):
            with pytest.raises(SessionError):
                resolve_session(conn, session_token=token, now=NOW)
        # Another user's session is untouched.
        assert (
            resolve_session(conn, session_token=other.session_token, now=NOW).user_id == VIEWER_ID
        )


@pytest.mark.parametrize("token", [None, "", "not-a-real-token"])
def test_absent_or_unknown_tokens_are_refused(db: str, token: str | None) -> None:
    with unscoped_connection(db) as conn, pytest.raises(SessionError):
        resolve_session(conn, session_token=token, now=NOW)


# --- CSRF -------------------------------------------------------------------------------------


def test_a_mutation_without_a_csrf_token_is_refused(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    with pytest.raises(SessionError, match="missing CSRF"):
        verify_csrf(session, method="POST", csrf_token=None)


def test_a_mutation_with_the_wrong_csrf_token_is_refused(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        other = issue_session(conn, user_id=VIEWER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    # Another live session's CSRF token must not work here.
    with pytest.raises(SessionError, match="does not match"):
        verify_csrf(session, method="POST", csrf_token=other.csrf_token)


def test_the_csrf_token_is_not_the_session_token(db: str) -> None:
    """A CSRF token derived from the session token protects nothing."""
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    assert issued.csrf_token != issued.session_token
    with pytest.raises(SessionError):
        verify_csrf(session, method="POST", csrf_token=issued.session_token)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_every_mutating_method_requires_csrf(db: str, method: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    with pytest.raises(SessionError):
        verify_csrf(session, method=method, csrf_token=None)
    verify_csrf(session, method=method, csrf_token=issued.csrf_token)  # allowed-path control


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_csrf(db: str, method: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OWNER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    verify_csrf(session, method=method, csrf_token=None)


# --- membership resolution --------------------------------------------------------------------


def test_a_member_resolves_to_their_actual_role(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=VIEWER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    with workspace_connection(db, WS_A) as conn:
        principal = resolve_human_principal(conn, session=session, workspace_id_from_route=WS_A)
    assert principal.role is Role.VIEWER
    require_permission(principal, Permission.EVIDENCE_READ)  # allowed-path control
    with pytest.raises(AuthorizationError):
        require_permission(principal, Permission.MEMBERSHIP_ADMINISTER)


def test_a_valid_session_for_another_workspace_resolves_to_nothing(db: str) -> None:
    """Cross-workspace substitution with a genuinely authenticated user.

    The outsider is a real user with a real session and owner rights — in workspace B. Naming
    workspace A in the route must not produce a principal.
    """
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=OUTSIDER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
    with workspace_connection(db, WS_A) as conn, pytest.raises(MembershipError):
        resolve_human_principal(conn, session=session, workspace_id_from_route=WS_A)


def test_revoked_membership_stops_resolving_at_once(db: str) -> None:
    """Stale membership during a long-lived session.

    The session is still valid; membership is not. Authority must be re-derived per request rather
    than captured at sign-in.
    """
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=VIEWER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)

    with workspace_connection(db, WS_A) as conn:
        assert (
            resolve_human_principal(conn, session=session, workspace_id_from_route=WS_A).role
            is Role.VIEWER
        )

    with workspace_connection(db, WS_A) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() WHERE user_id = %s", (VIEWER_ID,)
        )

    with workspace_connection(db, WS_A) as conn, pytest.raises(MembershipError):
        resolve_human_principal(conn, session=session, workspace_id_from_route=WS_A)


def test_a_disabled_user_stops_resolving(db: str) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=VIEWER_ID, now=NOW)
        session = resolve_session(conn, session_token=issued.session_token, now=NOW)
        conn.execute("UPDATE app_user SET disabled_at = now() WHERE id = %s", (VIEWER_ID,))
    with workspace_connection(db, WS_A) as conn, pytest.raises(MembershipError):
        resolve_human_principal(conn, session=session, workspace_id_from_route=WS_A)


def test_a_role_cannot_be_forged_by_naming_one(db: str) -> None:
    """There is no parameter through which a caller can assert a role.

    `resolve_human_principal` takes only the session and the route workspace; the role comes from
    the database. This test documents the absence as a property rather than relying on reviewers
    noticing it.
    """
    import inspect

    params = set(inspect.signature(resolve_human_principal).parameters)
    assert params == {"conn", "session", "workspace_id_from_route"}
    assert not any("role" in p for p in params)


def test_a_body_workspace_that_disagrees_with_the_route_is_refused() -> None:
    assert_route_matches_body(workspace_id_from_route=WS_A, workspace_id_from_body=None)
    assert_route_matches_body(workspace_id_from_route=WS_A, workspace_id_from_body=WS_A)
    with pytest.raises(AuthorizationError, match="does not match the route"):
        assert_route_matches_body(workspace_id_from_route=WS_A, workspace_id_from_body=WS_B)


# --- enrollment -------------------------------------------------------------------------------


def test_an_enrollment_token_works_exactly_once(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)

    with workspace_connection(db, WS_A) as conn:
        device = redeem_enrollment(
            conn, token=issued.token, label="mac-1", platform="darwin", now=NOW
        )
    assert device.workspace_id == WS_A

    with workspace_connection(db, WS_A) as conn, pytest.raises(EnrollmentError):
        redeem_enrollment(conn, token=issued.token, label="mac-2", platform="darwin", now=NOW)


def test_an_expired_enrollment_token_is_refused(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
    with workspace_connection(db, WS_A) as conn, pytest.raises(EnrollmentError):
        redeem_enrollment(
            conn,
            token=issued.token,
            label="mac-1",
            platform="darwin",
            now=NOW + timedelta(minutes=16),
        )


def test_a_revoked_enrollment_token_is_refused(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
        conn.execute(
            "UPDATE enrollment_credential SET revoked_at = now() WHERE id = %s",
            (issued.credential_id,),
        )
    with workspace_connection(db, WS_A) as conn, pytest.raises(EnrollmentError):
        redeem_enrollment(conn, token=issued.token, label="mac-1", platform="darwin", now=NOW)


def test_the_raw_enrollment_token_is_never_stored(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
        rows = conn.execute("SELECT token_hash FROM enrollment_credential").fetchall()
    assert issued.token not in " ".join(str(r["token_hash"]) for r in rows)


def test_admission_and_credential_revoke_independently(db: str) -> None:
    """A device barred from new work can still wind down work already in flight.

    Killing the credential instead would leave a run permanently ambiguous, because the device could
    no longer report that it had stopped.
    """
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
    with workspace_connection(db, WS_A) as conn:
        device = redeem_enrollment(
            conn, token=issued.token, label="mac-1", platform="darwin", now=NOW
        )
        assert device_may_be_admitted(conn, device_id=device.device_id)  # allowed-path control

        revoke_device_admission(conn, device_id=device.device_id, now=NOW)
        assert not device_may_be_admitted(conn, device_id=device.device_id)
        row = conn.execute(
            "SELECT credential_revoked_at FROM runner_device WHERE id = %s", (device.device_id,)
        ).fetchone()
        assert row is not None and row["credential_revoked_at"] is None

        revoke_device_credential(conn, device_id=device.device_id, now=NOW)
        assert not device_may_be_admitted(conn, device_id=device.device_id)


def test_an_enrollment_token_is_not_a_user_session(db: str) -> None:
    """A device credential presented where a human session is expected must fail."""
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
    with unscoped_connection(db) as conn, pytest.raises(SessionError):
        resolve_session(conn, session_token=issued.token, now=NOW)


def test_enrollment_is_confined_to_its_own_workspace(db: str) -> None:
    """Row-level security applies to enrollment too: a token minted for A is invisible in B."""
    with workspace_connection(db, WS_A) as conn:
        issued = create_enrollment(conn, workspace_id=WS_A, created_by=OWNER_ID, now=NOW)
    with workspace_connection(db, WS_B) as conn, pytest.raises(EnrollmentError):
        redeem_enrollment(conn, token=issued.token, label="mac-1", platform="darwin", now=NOW)


# --- audit ------------------------------------------------------------------------------------


def test_denials_are_recorded_not_only_allowances(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        record_audit_event(
            conn,
            workspace_id=WS_A,
            actor_user=VIEWER_ID,
            action="MEMBERSHIP_ADMINISTER",
            target_kind="workspace",
            target_id=WS_A,
            outcome="DENIED",
            detail={"reason": "role VIEWER"},
        )
    with workspace_connection(db, WS_A) as conn:
        row = conn.execute("SELECT outcome, detail FROM audit_event").fetchone()
    assert row is not None and row["outcome"] == "DENIED"
    assert row["detail"] == {"reason": "role VIEWER"}


def test_the_audit_table_has_no_column_for_a_secret(db: str) -> None:
    """Privacy by construction: a careless caller cannot write a token or transcript here."""
    with unscoped_connection(db) as conn:
        columns = {
            str(r["column_name"])
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='audit_event'"
            ).fetchall()
        }
    for forbidden in (
        "password",
        "token",
        "secret",
        "transcript",
        "speech",
        "gender",
        "disability",
    ):
        assert not any(forbidden in c for c in columns), f"audit_event exposes {forbidden}"


def test_audit_rows_are_workspace_isolated(db: str) -> None:
    with workspace_connection(db, WS_A) as conn:
        record_audit_event(
            conn,
            workspace_id=WS_A,
            action="SIGN_IN",
            target_kind="session",
            target_id=None,
            outcome="ALLOWED",
            actor_user=OWNER_ID,
        )
    with workspace_connection(db, WS_B) as conn:
        # No predicate. The earlier version of this test filtered on `workspace_id IS NOT NULL`,
        # which quietly hid the NULL-workspace carve-out that made sign-in rows readable by every
        # tenant. A test that avoids the failure it exists to catch is worse than no test.
        rows = conn.execute("SELECT * FROM audit_event").fetchall()
    assert rows == [], "workspace B must not read workspace A's audit trail"


def test_a_sign_in_is_recorded_where_no_tenant_can_read_it(db: str) -> None:
    """Workspace-independent events go to the operator-only table.

    Recording a sign-in into the workspace-scoped table was how an actor id and originating address
    became readable by every tenant.
    """
    from accessforge_api.auth import record_global_audit_event

    with unscoped_connection(db) as conn:
        record_global_audit_event(
            conn,
            action="SIGN_IN",
            target_kind="session",
            target_id=None,
            outcome="ALLOWED",
            actor_user=OWNER_ID,
            detail={"from": "198.51.100.7"},
        )

    for workspace in (WS_A, WS_B):
        with workspace_connection(db, workspace) as conn:
            assert conn.execute("SELECT * FROM global_audit_event").fetchall() == []
            assert conn.execute("SELECT * FROM audit_event").fetchall() == []

    with unscoped_connection(db) as conn:  # allowed-path control
        rows = conn.execute("SELECT action, detail FROM global_audit_event").fetchall()
    assert rows and rows[0]["action"] == "SIGN_IN"


def test_the_global_audit_table_also_has_no_column_for_a_secret(db: str) -> None:
    with unscoped_connection(db) as conn:
        columns = {
            str(r["column_name"])
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'global_audit_event'"
            ).fetchall()
        }
    for forbidden in (
        "password",
        "token",
        "secret",
        "transcript",
        "speech",
        "gender",
        "disability",
    ):
        assert not any(forbidden in c for c in columns), f"global_audit_event exposes {forbidden}"


def test_a_workspace_scoped_audit_row_now_requires_a_workspace(db: str) -> None:
    """The signature no longer permits the NULL that caused the leak."""
    import inspect

    from accessforge_api.auth import record_audit_event as scoped

    param = inspect.signature(scoped).parameters["workspace_id"]
    assert param.default is inspect.Parameter.empty, "workspace_id must be required"
    assert param.annotation == "str", f"workspace_id must not be optional, got {param.annotation}"
