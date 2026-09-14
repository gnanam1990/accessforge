"""Synthetic HTTP protocol, not real GitHub App access or token issuance."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from accessforge_orchestrator.github_access import Refused, RepositoryScope, inspect_repository

SCOPE = RepositoryScope(7, 42, 3, 13, "fixture-owner", "fixture-repository")
TOKEN = "ghs_" + "a" * 32


def transport(fault: str | None, calls: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "api.github.com" and request.url.scheme == "https"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        path = request.url.path
        value: dict[str, Any] = {}
        status = 200
        if path == "/app/installations/42" and request.method == "GET":
            assert request.headers["Authorization"] == "Bearer a.b.c"
            value = {
                "id": 42,
                "app_id": 7,
                "account": {"id": 3},
                "suspended_at": None,
                "permissions": {"metadata": "read", "contents": "write"},
            }
            if fault == "wrong-app":
                value["app_id"] = 8
            if fault == "wrong-installation":
                value["id"] = 43
            if fault == "suspended" or (fault == "late-suspension" and len(calls) > 1):
                value["suspended_at"] = "2026-09-14T00:00:00Z"
            if fault == "missing-permission":
                value["permissions"] = {"metadata": "read"}
        elif path == "/app/installations/42/access_tokens" and request.method == "POST":
            assert json.loads(request.content) == {
                "repository_ids": [13],
                "permissions": {"metadata": "read", "contents": "read"},
            }
            status = 201
            value = {
                "token": TOKEN,
                "permissions": {"metadata": "read", "contents": "read"},
                "expires_at": (datetime.now(UTC) + timedelta(minutes=59))
                .isoformat()
                .replace("+00:00", "Z"),
            }
            if fault == "overbroad":
                value["permissions"]["contents"] = "write"
            if fault == "expired":
                value["expires_at"] = "2000-01-01T00:00:00Z"
            if fault == "missing-expiry":
                del value["expires_at"]
            if fault == "issuance-timeout":
                raise httpx.ReadTimeout("private credential details must not escape")
        elif path == "/repos/fixture-owner/fixture-repository" and request.method == "GET":
            assert request.headers["Authorization"] == "Bearer " + TOKEN
            value = {"id": 13, "full_name": "fixture-owner/fixture-repository", "owner": {"id": 3}}
            if fault == "wrong-repository":
                value["id"] = 14
            if fault == "owner-changed":
                value["owner"]["id"] = 4
            if fault == "redirect":
                status = 302
        elif path == "/installation/token" and request.method == "DELETE":
            assert request.headers["Authorization"] == "Bearer " + TOKEN
            status = 500 if fault == "revocation-failed" else 204
        else:
            pytest.fail("unexpected request")
        return httpx.Response(
            status,
            stream=httpx.ByteStream(json.dumps(value).encode()),
            headers={"Content-Type": "application/json"},
        )

    return httpx.MockTransport(handle)


def test_exact_read_probe_revokes_and_never_returns_credential() -> None:
    calls: list[httpx.Request] = []
    result = inspect_repository(
        SCOPE,
        app_jwt="a.b.c",
        allow_temporary_token_issuance=True,
        _transport=transport(None, calls),
    )
    assert result.scope == SCOPE and result.token_revoked
    assert result.meaning == "POINT_IN_TIME_READ_ACCESS_NOT_PUBLICATION_AUTHORITY"
    assert TOKEN not in repr(result) and "a.b.c" not in repr(result)
    assert [call.method for call in calls] == ["GET", "POST", "GET", "GET", "DELETE"]


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-app",
        "wrong-installation",
        "suspended",
        "missing-permission",
        "late-suspension",
        "overbroad",
        "expired",
        "missing-expiry",
        "issuance-timeout",
        "wrong-repository",
        "owner-changed",
        "redirect",
        "revocation-failed",
    ],
)
def test_failed_scope_or_uncertain_network_never_returns_access(fault: str) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused) as error:
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            _transport=transport(fault, calls),
        )
    assert "private credential" not in str(error.value) and TOKEN not in str(error.value)
    assert sum(call.method == "POST" for call in calls) <= 1
    if fault in {
        "overbroad",
        "expired",
        "missing-expiry",
        "wrong-repository",
        "owner-changed",
        "redirect",
        "late-suspension",
        "revocation-failed",
    }:
        assert calls[-1].method == "DELETE"


def test_missing_explicit_issuance_authority_makes_no_request() -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused, match="explicit authorization"):
        inspect_repository(SCOPE, app_jwt="a.b.c", _transport=transport(None, calls))
    assert calls == []


@pytest.mark.parametrize(
    "owner,name", [("..", "repo"), ("owner", "../../repo"), ("https://evil.test", "repo")]
)
def test_scope_is_not_a_user_selected_url(owner: str, name: str) -> None:
    with pytest.raises(Refused):
        RepositoryScope(7, 42, 3, 13, owner, name)
