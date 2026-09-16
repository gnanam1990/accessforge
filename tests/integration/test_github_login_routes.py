"""ASGI + real PostgreSQL login flow; provider identity is synthetic, no browser/AT."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth.github_accounts import bind_account
from accessforge_api.auth.github_identity import GitHubIdentityError, GitHubSubject
from accessforge_api.config import ApiSettings
from accessforge_api.routes.github_login import LOGIN_COOKIE
from accessforge_persistence import migrate, unscoped_connection

pytestmark = pytest.mark.integration
BASE = "https://app.example.test"


@pytest.fixture()
def client(test_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    migrate(test_database_url)
    user = str(uuid.uuid4())
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE app_user CASCADE")
        conn.execute("TRUNCATE github_login_challenge")
        conn.execute("INSERT INTO app_user(id,email) VALUES(%s,'login@example.test')", (user,))
    bind_account(test_database_url, github_subject="123", user_id=user, operator="route-test")
    settings_values: dict[str, Any] = dict(
        _env_file=None,
        database_url=test_database_url,
        environment="local",
        evidence_endpoint_url="http://127.0.0.1:9000",
        evidence_bucket="unused",
        evidence_access_key="test-key-value",
        evidence_secret_key="test-key-value",
        identity_provider="github",
        github_oauth_client_id="test-client",
        github_oauth_client_secret="a" * 32,
        github_oauth_redirect_uri=BASE + "/v1/auth/github/callback",
    )
    app = create_app(ApiSettings(**settings_values))
    app.state.identity_calls = 0

    async def synthetic_identity(*args: object, **kwargs: object) -> GitHubSubject:
        app.state.identity_calls += 1
        with unscoped_connection(test_database_url) as conn:
            row = conn.execute("SELECT consumed_at FROM github_login_challenge").fetchone()
            assert row is not None and row["consumed_at"] is not None
        return GitHubSubject("123")

    monkeypatch.setattr("accessforge_api.routes.github_login.exchange_identity", synthetic_identity)
    with TestClient(app, base_url=BASE, follow_redirects=False) as result:
        yield result


def _app(client: TestClient) -> FastAPI:
    assert isinstance(client.app, FastAPI)
    return client.app


def _start(client: TestClient) -> tuple[str, str]:
    response = client.get("/v1/auth/github/start")
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.netloc == "github.com"
    cookie = client.cookies.get(LOGIN_COOKIE)
    assert cookie is not None
    header = response.headers["set-cookie"]
    assert "Secure" in header and "HttpOnly" in header and "SameSite=lax" in header
    assert "Domain=" not in header and "Path=/" in header
    return parse_qs(location.query)["state"][0], cookie


def test_callback_commits_state_then_identity_then_audited_session(client: TestClient) -> None:
    state, cookie = _start(client)
    result = client.get("/v1/auth/github/callback", params={"state": state, "code": "valid-code"})
    assert result.status_code == 303 and result.headers["location"] == "/"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert LOGIN_COOKIE not in client.cookies
    assert client.get("/v1/session").status_code == 200
    assert _app(client).state.identity_calls == 1
    # Restore the exact original browser cookie, not the already-cleared jar.
    replay = client.get(
        "/v1/auth/github/callback",
        params={"state": state, "code": "valid-code"},
        headers={"cookie": f"{LOGIN_COOKIE}={cookie}"},
    )
    assert replay.status_code == 401
    assert _app(client).state.identity_calls == 1


@pytest.mark.parametrize(
    "failure", ["state", "cookie", "duplicate", "query-verifier", "host", "http"]
)
def test_callback_refuses_before_provider_on_browser_binding_failure(
    client: TestClient,
    failure: str,
) -> None:
    state, cookie = _start(client)
    params = [("state", state), ("code", "valid-code")]
    headers = {"cookie": f"{LOGIN_COOKIE}={cookie}"}
    url = BASE + "/v1/auth/github/callback"
    if failure == "state":
        params[0] = ("state", "z" * 43)
    elif failure == "cookie":
        headers["cookie"] = f"{LOGIN_COOKIE}={'z' * 43}.{'y' * 43}"
    elif failure == "duplicate":
        params.append(("state", state))
    elif failure == "query-verifier":
        params.append(("code_verifier", cookie.split(".")[1]))
    elif failure == "host":
        url = "https://wrong.example.test/v1/auth/github/callback"
    else:
        url = "http://app.example.test/v1/auth/github/callback"
    result = client.get(url, params=urlencode(params), headers=headers)
    assert result.status_code == 401
    assert result.headers["cache-control"] == "no-store"
    assert "Max-Age=0" in result.headers["set-cookie"]
    assert _app(client).state.identity_calls == 0


def test_provider_failure_burns_state_and_redacts_details(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state, cookie = _start(client)

    async def fail(*args: object, **kwargs: object) -> GitHubSubject:
        raise GitHubIdentityError("PRIVATE_PROVIDER_ERROR")

    monkeypatch.setattr("accessforge_api.routes.github_login.exchange_identity", fail)
    result = client.get("/v1/auth/github/callback", params={"state": state, "code": "PRIVATE_CODE"})
    assert result.status_code == 401
    assert "PRIVATE_PROVIDER_ERROR" not in result.text + caplog.text
    # ASGI's route-template telemetry must not expose query credentials.
    records = [r.message for r in caplog.records if r.name == "accessforge.telemetry"]
    assert "PRIVATE_CODE" not in "".join(records)
    with unscoped_connection(_app(client).state.config.database_url) as conn:
        assert conn.execute("SELECT * FROM user_session").fetchall() == []
        row = conn.execute("SELECT consumed_at FROM github_login_challenge").fetchone()
        assert row is not None and row["consumed_at"] is not None
    assert cookie not in result.text


def test_github_mode_cannot_fall_through_to_passwordless_email_login(client: TestClient) -> None:
    assert client.post("/v1/sessions", json={"email": "login@example.test"}).status_code == 401


def test_cross_site_start_and_duplicate_cookie_are_refused(client: TestClient) -> None:
    assert (
        client.get("/v1/auth/github/start", headers={"sec-fetch-site": "cross-site"}).status_code
        == 401
    )
    state, cookie = _start(client)
    result = client.get(
        "/v1/auth/github/callback",
        params={"state": state, "code": "valid-code"},
        headers={"cookie": f"{LOGIN_COOKIE}={cookie}; {LOGIN_COOKIE}={cookie}"},
    )
    assert result.status_code == 401


def test_provider_denial_consumes_without_exchange(client: TestClient) -> None:
    state, _ = _start(client)
    result = client.get(
        "/v1/auth/github/callback",
        params={
            "state": state,
            "error": "access_denied",
            "error_description": "PRIVATE_PROVIDER_DESCRIPTION",
        },
    )
    assert result.status_code == 401 and "PRIVATE_PROVIDER_DESCRIPTION" not in result.text
    assert _app(client).state.identity_calls == 0


def test_disabled_provider_refuses_both_routes(client: TestClient) -> None:
    _app(client).state.config.identity_provider = "none"
    for path in ("start", "callback"):
        response = client.get("/v1/auth/github/" + path)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"


def test_global_admission_limit_and_expired_cleanup(client: TestClient) -> None:
    with unscoped_connection(_app(client).state.config.database_url) as conn:
        conn.execute(
            "INSERT INTO github_login_challenge(state_hash,browser_hash,verifier_hash,"
            "configuration_hash,"
            "created_at,expires_at) SELECT lpad(to_hex(n),64,'0'),repeat('b',64),repeat('c',64),"
            "repeat('d',64),now(),now()+interval '5 minutes' FROM generate_series(1,120) n"
        )
    limited = client.get("/v1/auth/github/start")
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    assert LOGIN_COOKIE not in client.cookies
    with unscoped_connection(_app(client).state.config.database_url) as conn:
        conn.execute(
            "UPDATE github_login_challenge SET created_at=now()-interval '6 minutes', "
            "expires_at=now()-interval '1 minute'"
        )
    _start(client)
    with unscoped_connection(_app(client).state.config.database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_login_challenge").fetchone() == {
            "n": 1
        }


def test_another_replica_holding_admission_lock_refuses_without_waiting(client: TestClient) -> None:
    with unscoped_connection(_app(client).state.config.database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(713021,1)")
        result = client.get("/v1/auth/github/start")
        assert result.status_code == 429
        assert conn.execute("SELECT * FROM github_login_challenge").fetchall() == []
    _start(client)
