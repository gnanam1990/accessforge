"""Real database identity/session checks, not actual OAuth or reader acceptance."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from accessforge_api.auth.github_accounts import bind_account, issue_github_session, revoke_binding
from accessforge_api.auth.github_identity import GitHubIdentityError, GitHubSubject
from accessforge_api.auth.sessions import (
    SessionError,
    issue_session,
    resolve_session,
    rotate_session,
)
from accessforge_persistence import migrate, unscoped_connection, user_connection

pytestmark = pytest.mark.integration
SUBJECT = "123456789"


@pytest.fixture()
def account(test_database_url: str) -> tuple[str, str]:
    migrate(test_database_url)
    user = str(uuid.uuid4())
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE app_user CASCADE")
        conn.execute("INSERT INTO app_user(id,email) VALUES(%s,'identity@example.test')", (user,))
    return test_database_url, user


def _bind(db: str, user: str, subject: str = SUBJECT) -> None:
    bind_account(db, github_subject=subject, user_id=user, operator="test-operator")


def test_binding_requires_existing_enabled_user_and_no_auto_link(account: tuple[str, str]) -> None:
    db, user = account
    with pytest.raises(GitHubIdentityError):
        issue_github_session(db, GitHubSubject(SUBJECT))
    with pytest.raises(GitHubIdentityError):
        _bind(db, str(uuid.uuid4()))
    with unscoped_connection(db) as conn:
        conn.execute("UPDATE app_user SET disabled_at=now() WHERE id=%s", (user,))
    with pytest.raises(GitHubIdentityError):
        _bind(db, user)


def test_revocation_survives_rotation_without_affecting_local_sessions(
    account: tuple[str, str],
) -> None:
    db, user = account
    _bind(db, user)
    issued = issue_github_session(db, GitHubSubject(SUBJECT))
    with unscoped_connection(db) as conn:
        local = issue_session(conn, user_id=user)
        authenticated = resolve_session(conn, session_token=issued.session_token)
        rotated = rotate_session(conn, session=authenticated)
        row = conn.execute(
            "SELECT github_subject FROM user_session WHERE id=%s", (rotated.session_id,)
        ).fetchone()
        assert row == {"github_subject": int(SUBJECT)}
    revoke_binding(db, github_subject=SUBJECT, operator="test-operator")
    with pytest.raises(GitHubIdentityError):
        issue_github_session(db, GitHubSubject(SUBJECT))
    with pytest.raises(GitHubIdentityError):
        _bind(db, user)  # no silent reactivation
    with unscoped_connection(db) as conn:
        for token in (issued.session_token, rotated.session_token):
            with pytest.raises(SessionError):
                resolve_session(conn, session_token=token)
        assert resolve_session(conn, session_token=local.session_token).user_id == user
        events = conn.execute(
            "SELECT action FROM global_audit_event WHERE target_id=%s ORDER BY occurred_at", (user,)
        ).fetchall()
        assert events == [
            {"action": "github_identity.bound"},
            {"action": "github_identity.revoked"},
        ]
    with user_connection(db, user) as conn:
        assert conn.execute("SELECT * FROM workspace_membership").fetchall() == []


@pytest.mark.parametrize("mutation", ["disable", "revoke"])
def test_resolution_checks_current_account_and_binding_state(
    account: tuple[str, str],
    mutation: str,
) -> None:
    db, user = account
    _bind(db, user)
    issued = issue_github_session(db, GitHubSubject(SUBJECT))
    with unscoped_connection(db) as conn:
        if mutation == "disable":
            conn.execute("UPDATE app_user SET disabled_at=now() WHERE id=%s", (user,))
        else:
            # Prove resolution checks the binding, not only the revocation helper's update.
            conn.execute(
                "UPDATE github_user_identity SET revoked_at=now() WHERE github_subject=%s",
                (int(SUBJECT),),
            )
    with pytest.raises(GitHubIdentityError):
        issue_github_session(db, GitHubSubject(SUBJECT))
    with unscoped_connection(db) as conn:
        with pytest.raises(SessionError):
            resolve_session(conn, session_token=issued.session_token)


def test_concurrent_bindings_never_reassign_a_subject(account: tuple[str, str]) -> None:
    db, user = account
    other = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO app_user(id,email) VALUES(%s,'other@example.test')", (other,))
    barrier = Barrier(2)

    def attempt(local_user: str) -> bool:
        barrier.wait(timeout=5)
        try:
            _bind(db, local_user)
        except GitHubIdentityError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, local_user) for local_user in (user, other)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]
    with unscoped_connection(db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_user_identity").fetchone() == {"n": 1}


def test_one_user_cannot_be_bound_to_two_subjects(account: tuple[str, str]) -> None:
    db, user = account
    _bind(db, user)
    with pytest.raises(GitHubIdentityError):
        _bind(db, user, "987654321")


@pytest.mark.parametrize("operation", ["issue", "rotate"])
def test_revocation_race_leaves_no_usable_github_session(
    account: tuple[str, str],
    operation: str,
) -> None:
    db, user = account
    _bind(db, user)
    original = issue_github_session(db, GitHubSubject(SUBJECT))
    barrier = Barrier(2)

    def acquire() -> str | None:
        barrier.wait(timeout=5)
        try:
            if operation == "issue":
                return issue_github_session(db, GitHubSubject(SUBJECT)).session_token
            with unscoped_connection(db) as conn:
                conn.execute("SET LOCAL statement_timeout = '5s'")
                session = resolve_session(conn, session_token=original.session_token)
                return rotate_session(conn, session=session).session_token
        except (GitHubIdentityError, SessionError):
            return None

    def revoke() -> None:
        barrier.wait(timeout=5)
        revoke_binding(db, github_subject=SUBJECT, operator="test-operator")

    with ThreadPoolExecutor(max_workers=2) as pool:
        acquiring, revoking = pool.submit(acquire), pool.submit(revoke)
        token = acquiring.result(timeout=10)
        revoking.result(timeout=10)
    with unscoped_connection(db) as conn:
        for value in (original.session_token, token):
            if value is not None:
                with pytest.raises(SessionError):
                    resolve_session(conn, session_token=value)


@pytest.mark.parametrize("operation", ["bind", "issue", "revoke"])
def test_audit_failure_rolls_back_the_identity_operation(
    account: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    db, user = account
    if operation != "bind":
        _bind(db, user)
    issued = issue_github_session(db, GitHubSubject(SUBJECT)) if operation == "revoke" else None

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "accessforge_api.auth.github_accounts.record_global_audit_event", fail_audit
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        if operation == "bind":
            _bind(db, user)
        elif operation == "issue":
            issue_github_session(db, GitHubSubject(SUBJECT))
        else:
            revoke_binding(db, github_subject=SUBJECT, operator="test-operator")
    with unscoped_connection(db) as conn:
        if operation == "bind":
            assert conn.execute("SELECT * FROM github_user_identity").fetchall() == []
        elif operation == "issue":
            assert conn.execute("SELECT * FROM user_session").fetchall() == []
        else:
            assert issued is not None
            assert resolve_session(conn, session_token=issued.session_token).user_id == user
