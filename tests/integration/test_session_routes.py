"""Sign-in, the session context the web shell reads, and sign-out.

Through the real ASGI app against a real database. The three routes exist because module 21's shell
needs them; the tests here are mostly about the ways each one could quietly become a security
problem.

* A deployment with no identity provider must refuse to sign anyone in, and must say *why* — a
  missing dependency is not a rejected credential.
* The local-development bridge must not become an account-existence oracle, and must not be
  startable outside a local environment.
* The session context must reflect membership **now**, so a revocation is visible on the next read
  rather than on the next sign-in.
* Sign-out must revoke the session server-side, not merely clear a cookie, and must be CSRF-checked
  like any other mutation.

Requirements: FR-014, FR-016. Invariants: INV-07, INV-12.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_api.routes.session import CSRF_COOKIE
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS_A = str(uuid.UUID(int=0x210))
WS_B = str(uuid.UUID(int=0x211))
MEMBER = str(uuid.UUID(int=0x212))
MEMBER_EMAIL = "member@example.test"
DISABLED = str(uuid.UUID(int=0x213))
DISABLED_EMAIL = "disabled@example.test"
STRANGER_EMAIL = "nobody@example.test"


def _settings(url: str, **overrides: object) -> ApiSettings:
    base: dict[str, object] = {
        "database_url": url,
        "evidence_endpoint_url": os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        "evidence_bucket": os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        "evidence_access_key": os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        "evidence_secret_key": os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        "environment": "local",
    }
    base.update(overrides)
    return ApiSettings(**base)  # type: ignore[arg-type]


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        # Deliberately inserted out of alphabetical order, so an unordered answer would not happen
        # to look sorted.
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Zebra')", (WS_B,))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Alder')", (WS_A,))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (MEMBER, MEMBER_EMAIL))
        conn.execute(
            "INSERT INTO app_user (id, email, disabled_at) VALUES (%s, %s, now())",
            (DISABLED, DISABLED_EMAIL),
        )
    for workspace, role in ((WS_A, "OWNER"), (WS_B, "VIEWER")):
        with workspace_connection(test_database_url, workspace) as conn:
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, %s)",
                (workspace, MEMBER, role),
            )
    yield test_database_url


@pytest.fixture()
def local_client(db: str) -> Iterator[TestClient]:
    """A client whose deployment has the local-development sign-in bridge enabled."""
    with TestClient(create_app(_settings(db, identity_provider="local-development"))) as client:
        yield client


@pytest.fixture()
def closed_client(db: str) -> Iterator[TestClient]:
    """A client whose deployment has no identity provider, which is the default."""
    with TestClient(create_app(_settings(db))) as client:
        yield client


# --------------------------------------------------------------------------------------------------
# A deployment with no provider cannot sign anyone in, and says so accurately
# --------------------------------------------------------------------------------------------------


def test_the_default_deployment_has_no_identity_provider(db: str) -> None:
    assert _settings(db).identity_provider == "none"


def test_sign_in_without_a_provider_is_a_missing_dependency_not_a_bad_credential(
    closed_client: TestClient,
) -> None:
    response = closed_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "DEPENDENCY_UNAVAILABLE"
    # 401 would tell the operator to check their credentials, which is the wrong instruction: no
    # credential could have worked.
    assert "no identity provider is configured" in body["detail"]
    assert SESSION_COOKIE not in response.cookies


def test_a_nonlocal_environment_refuses_to_start_with_the_development_bridge(db: str) -> None:
    for environment in ("test", "staging", "production"):
        with pytest.raises(ValidationError) as raised:
            _settings(
                db,
                environment=environment,
                identity_provider="local-development",
                evidence_endpoint_url="https://example.invalid",
                database_url="postgresql://u:p@db.example.invalid/x",
            )
        assert "local-development" in str(raised.value)


# --------------------------------------------------------------------------------------------------
# The local-development bridge
# --------------------------------------------------------------------------------------------------


def test_sign_in_issues_a_session_cookie_and_a_readable_csrf_cookie(
    local_client: TestClient,
) -> None:
    response = local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    assert response.status_code == 201
    assert response.json()["userId"] == MEMBER

    jar = {c.name: c for c in local_client.cookies.jar}
    assert set(jar) >= {SESSION_COOKIE, CSRF_COOKIE}
    # The session cookie must be unreadable by script; the CSRF cookie must be readable, because the
    # browser has to echo it into a header the server can compare.
    raw = response.headers.get_list("set-cookie")
    session_header = next(h for h in raw if h.startswith(f"{SESSION_COOKIE}="))
    csrf_header = next(h for h in raw if h.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" in session_header
    assert "HttpOnly" not in csrf_header
    assert "SameSite=lax" in session_header.lower().replace("samesite=lax", "SameSite=lax")


def test_the_response_body_does_not_repeat_the_tokens(local_client: TestClient) -> None:
    response = local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    jar = {c.name: c.value for c in local_client.cookies.jar}
    serialised = response.text
    for token in (jar[SESSION_COOKIE], jar[CSRF_COOKIE]):
        assert token not in serialised


def test_an_unknown_and_a_disabled_account_are_answered_identically(
    local_client: TestClient,
) -> None:
    unknown = local_client.post("/v1/sessions", json={"email": STRANGER_EMAIL})
    disabled = local_client.post("/v1/sessions", json={"email": DISABLED_EMAIL})
    assert unknown.status_code == disabled.status_code == 401
    # Identical down to the prose. A difference of any kind is an account-existence oracle, and this
    # route would be the easiest one in the product to enumerate users with.
    assert unknown.json()["detail"] == disabled.json()["detail"]
    assert unknown.json()["code"] == disabled.json()["code"] == "NOT_AUTHENTICATED"


def test_sign_in_is_case_insensitive_in_the_address_only(local_client: TestClient) -> None:
    response = local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL.upper()})
    assert response.status_code == 201


def test_sign_in_refuses_an_unexpected_field(local_client: TestClient) -> None:
    response = local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL, "role": "OWNER"})
    assert response.status_code == 400
    assert response.json()["code"] == "UNEXPECTED_FIELD"


def test_sign_in_refuses_an_absurdly_long_address(local_client: TestClient) -> None:
    response = local_client.post("/v1/sessions", json={"email": "a" * 400 + "@example.test"})
    assert response.status_code == 400


def test_a_denied_sign_in_is_recorded_in_the_operator_audit_trail(
    local_client: TestClient, db: str
) -> None:
    local_client.post("/v1/sessions", json={"email": STRANGER_EMAIL})
    with unscoped_connection(db) as conn:
        rows = conn.execute(
            "SELECT outcome, target_id FROM global_audit_event WHERE action = 'session.sign-in'"
        ).fetchall()
    assert [(r["outcome"], r["target_id"]) for r in rows] == [("DENIED", None)]


# --------------------------------------------------------------------------------------------------
# The session context the shell reads
# --------------------------------------------------------------------------------------------------


def test_reading_the_session_without_a_cookie_is_401(closed_client: TestClient) -> None:
    response = closed_client.get("/v1/session")
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_the_session_reports_every_live_membership_with_its_role(local_client: TestClient) -> None:
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    body = local_client.get("/v1/session").json()
    assert body["userId"] == MEMBER
    assert body["email"] == MEMBER_EMAIL
    assert [(w["name"], w["role"]) for w in body["workspaces"]] == [
        ("Alder", "OWNER"),
        ("Zebra", "VIEWER"),
    ]


def test_a_revoked_membership_disappears_from_the_session_immediately(
    local_client: TestClient, db: str
) -> None:
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    assert len(local_client.get("/v1/session").json()["workspaces"]) == 2

    with workspace_connection(db, WS_B) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() "
            "WHERE workspace_id = %s AND user_id = %s",
            (WS_B, MEMBER),
        )

    # Without a fresh sign-in. A navigation built from a cached membership list is a navigation that
    # offers a workspace the server will refuse, and the refusal is a 404 the user cannot explain.
    remaining = local_client.get("/v1/session").json()["workspaces"]
    assert [w["workspaceId"] for w in remaining] == [WS_A]


def test_the_session_does_not_reveal_a_workspace_the_user_is_not_in(
    local_client: TestClient, db: str
) -> None:
    with unscoped_connection(db) as conn:
        other = str(uuid.UUID(int=0x214))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Secret')", (other,))
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    body = local_client.get("/v1/session")
    assert "Secret" not in body.text


def test_a_disabled_account_cannot_read_a_session_issued_before_it_was_disabled(
    closed_client: TestClient, db: str
) -> None:
    with unscoped_connection(db) as conn:
        issued = issue_session(conn, user_id=MEMBER)
    closed_client.cookies.set(SESSION_COOKIE, issued.session_token)
    assert closed_client.get("/v1/session").status_code == 200

    with unscoped_connection(db) as conn:
        conn.execute("UPDATE app_user SET disabled_at = now() WHERE id = %s", (MEMBER,))
    response = closed_client.get("/v1/session")
    assert response.status_code == 401


# --------------------------------------------------------------------------------------------------
# Sign-out
# --------------------------------------------------------------------------------------------------


def test_sign_out_revokes_the_session_in_the_database_not_only_the_cookie(
    local_client: TestClient, db: str
) -> None:
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    token = next(c.value for c in local_client.cookies.jar if c.name == SESSION_COOKIE)
    csrf = next(c.value for c in local_client.cookies.jar if c.name == CSRF_COOKIE)

    assert local_client.delete("/v1/session", headers={CSRF_HEADER: csrf}).status_code == 204

    # The cookie is gone from this client, which proves nothing: a copy of the token taken before
    # sign-out must also stop working.
    local_client.cookies.clear()
    local_client.cookies.set(SESSION_COOKIE, token)
    assert local_client.get("/v1/session").status_code == 401


def test_sign_out_clears_both_cookies_and_not_just_the_last_one(
    local_client: TestClient,
) -> None:
    """Both `Set-Cookie` deletions must survive to the wire.

    The first version of this route built a new response from ``dict(response.headers)``, and a
    dict keeps one value per name — so one of the two deletions vanished and the browser kept a
    cookie the server believed it had cleared. Every status assertion passed. Counting the headers
    is what catches it.
    """
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    csrf = next(c.value for c in local_client.cookies.jar if c.name == CSRF_COOKIE)
    response = local_client.delete("/v1/session", headers={CSRF_HEADER: csrf})

    assert response.status_code == 204
    cleared = [h for h in response.headers.get_list("set-cookie")]
    assert len(cleared) == 2, cleared
    assert {h.split("=", 1)[0] for h in cleared} == {SESSION_COOKIE, CSRF_COOKIE}
    # Cleared, not merely replaced: an expiry in the past is what removes a cookie from a browser.
    for header in cleared:
        assert "Max-Age=0" in header or "expires=Thu, 01 Jan 1970" in header.lower(), header


def test_sign_out_without_a_csrf_token_is_refused(local_client: TestClient) -> None:
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    response = local_client.delete("/v1/session")
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_REQUIRED"
    # And the session still works, because a refused request must not have partially acted.
    assert local_client.get("/v1/session").status_code == 200


def test_sign_out_with_no_session_succeeds_and_clears_the_cookies(
    closed_client: TestClient,
) -> None:
    # 401 here would leave a browser holding a cookie it cannot use and no way to discard it.
    response = closed_client.delete("/v1/session")
    assert response.status_code == 204


def test_sign_out_is_recorded_in_the_operator_audit_trail(
    local_client: TestClient, db: str
) -> None:
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    csrf = next(c.value for c in local_client.cookies.jar if c.name == CSRF_COOKIE)
    local_client.delete("/v1/session", headers={CSRF_HEADER: csrf})
    with unscoped_connection(db) as conn:
        rows = conn.execute(
            "SELECT outcome FROM global_audit_event WHERE action = 'session.sign-out'"
        ).fetchall()
    assert [r["outcome"] for r in rows] == ["ALLOWED"]


def test_there_is_no_route_that_reads_a_session_token_back(local_client: TestClient) -> None:
    """A structural check, not a behavioural one.

    `GET /v1/session` returns who you are, never the credential that established it. A route that
    could hand back a live token would turn any read-only disclosure into account takeover.
    """
    local_client.post("/v1/sessions", json={"email": MEMBER_EMAIL})
    token = next(c.value for c in local_client.cookies.jar if c.name == SESSION_COOKIE)
    assert token not in local_client.get("/v1/session").text
