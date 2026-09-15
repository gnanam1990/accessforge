"""Synthetic HTTP protocol, not real GitHub App access or token issuance."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from accessforge_domain.canonical import digest
from accessforge_orchestrator.github_access import Refused, RepositoryScope, inspect_repository

SCOPE = RepositoryScope(7, 42, 3, 13, "fixture-owner", "fixture-repository")
TOKEN = "ghs_" + "a" * 32
COMMIT = "a" * 40
CHECK_BODY: dict[str, Any] = {
    "name": "AccessForge journey evidence",
    "head_sha": COMMIT,
    "external_id": "accessforge:" + "b" * 64,
    "status": "completed",
    "conclusion": "action_required",
    "output": {"title": "Fixture", "summary": "Scope only"},
}


def transport(
    fault: str | None, calls: list[httpx.Request], *, check: bool = False, token: str = TOKEN
) -> httpx.MockTransport:
    permissions = {"metadata": "read", "contents": "read"}
    if check:
        permissions["checks"] = "read"

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
                "permissions": {**permissions, "contents": "write"},
            }
            if fault == "wrong-app":
                value["app_id"] = 8
            if fault == "wrong-installation":
                value["id"] = 43
            if fault == "suspended" or (fault == "late-suspension" and len(calls) > 1):
                value["suspended_at"] = "2026-09-14T00:00:00Z"
            if fault == "missing-permission":
                value["permissions"] = {"metadata": "read"}
            if fault == "missing-checks":
                del value["permissions"]["checks"]
        elif path == "/app/installations/42/access_tokens" and request.method == "POST":
            assert json.loads(request.content) == {
                "repository_ids": [13],
                "permissions": permissions,
            }
            status = 201
            value = {
                "token": token,
                "permissions": dict(permissions),
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
        elif path == "/repos/fixture-owner/fixture-repository/check-runs/91":
            assert check and request.method == "GET"
            assert request.headers["Authorization"] == "Bearer " + token
            value = {
                **CHECK_BODY,
                "id": 91,
                "app": {"id": 7},
                "output": {**CHECK_BODY["output"], "annotations_count": 0},
                "html_url": "https://untrusted.invalid/private-response",
            }
            if fault == "check-id":
                value["id"] = 92
            if fault == "check-app":
                value["app"]["id"] = 8
            if fault == "check-source":
                value["head_sha"] = "b" * 40
            if fault == "check-text":
                value["output"]["text"] = "unreviewed body"
            if fault == "check-annotations":
                value["output"]["annotations_count"] = 1
            if fault == "check-lifecycle":
                value["status"] = "in_progress"
            if fault == "check-missing":
                status = 404
            if fault == "check-redirect":
                status = 302
            if fault == "check-timeout":
                raise httpx.ReadTimeout("private check details must not escape")
            if fault == "changed-summary":
                value["output"]["summary"] = "Changed summary"
        elif path == f"/repos/fixture-owner/fixture-repository/git/commits/{COMMIT}":
            assert request.headers["Authorization"] == "Bearer " + token
            value = {
                "sha": "b" * 40 if fault == "wrong-commit" else COMMIT,
                "message": "untrusted commit text must not escape",
            }
            if fault == "missing-commit":
                status = 404
            if fault == "commit-redirect":
                status = 302
        elif path == "/repos/fixture-owner/fixture-repository" and request.method == "GET":
            assert request.headers["Authorization"] == "Bearer " + token
            value = {"id": 13, "full_name": "fixture-owner/fixture-repository", "owner": {"id": 3}}
            if fault == "wrong-repository":
                value["id"] = 14
            if fault == "owner-changed":
                value["owner"]["id"] = 4
            if fault == "redirect":
                status = 302
            if fault == "late-transfer" and len(calls) > 3:
                value["owner"]["id"] = 4
        elif path == "/installation/token" and request.method == "DELETE":
            assert request.headers["Authorization"] == "Bearer " + token
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
    "token",
    [
        TOKEN,
        "ghs_7_eyJhbGciOiJIUzI1NiJ9.eyJmaXh0dXJlIjp0cnVlfQ.test-signature_with-dashes",
        "ghs_7_" + "a" * 1500 + "." + "b" * 1500 + ".test-signature",
        "ghs_" + "a" * (8192 - 4),
    ],
    ids=["legacy", "stateless", "long-stateless", "header-boundary"],
)
def test_opaque_token_formats_preserve_scope_checks_and_cleanup(token: str) -> None:
    calls: list[httpx.Request] = []
    result = inspect_repository(
        SCOPE,
        app_jwt="a.b.c",
        allow_temporary_token_issuance=True,
        commit_sha=COMMIT,
        check_run_id=91,
        _transport=transport(None, calls, check=True, token=token),
    )
    assert result.token_revoked and result.check_observation is not None
    assert result.check_observation.payload_digest == digest(CHECK_BODY)
    assert token not in repr(result)
    assert calls[-1].method == "DELETE"
    assert calls[-1].headers["Authorization"] == "Bearer " + token


@pytest.mark.parametrize(
    "token",
    [
        "",
        "short",
        "ghs_" + "a" * 8189,
        "ghs_" + "a" * 30 + "\r\nX-Injected: yes",
        "ghs_" + "a" * 30 + " ",
        "ghs_" + "a" * 30 + "\x00",
        "ghs_" + "a" * 30 + "é",
        "ghs_" + "a" * 30 + "=invalid-padding",
    ],
)
def test_unsafe_or_oversized_token_never_enters_an_authorization_header(token: str) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused):
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            _transport=transport(None, calls, token=token),
        )
    assert [call.method for call in calls] == ["GET", "POST"]
    # An unusable issuance response is unconfirmed; never put unsafe bytes into cleanup headers.
    assert all(call.headers["Authorization"] == "Bearer a.b.c" for call in calls)


@pytest.mark.parametrize("fault", [None, "changed-summary"])
def test_known_check_read_returns_only_bound_payload_digest(fault: str | None) -> None:
    calls: list[httpx.Request] = []
    result = inspect_repository(
        SCOPE,
        app_jwt="a.b.c",
        allow_temporary_token_issuance=True,
        commit_sha=COMMIT,
        check_run_id=91,
        _transport=transport(fault, calls, check=True),
    )
    assert result.token_revoked and result.check_observation is not None
    assert result.check_observation.check_run_id == 91
    assert (result.check_observation.payload_digest == digest(CHECK_BODY)) is (fault is None)
    assert "private-response" not in repr(result) and "Scope only" not in repr(result)
    assert TOKEN not in repr(result)
    assert [call.method for call in calls] == [
        "GET",
        "POST",
        "GET",
        "GET",
        "GET",
        "GET",
        "GET",
        "DELETE",
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "check-id",
        "check-app",
        "check-source",
        "check-text",
        "check-annotations",
        "check-lifecycle",
        "check-missing",
        "check-redirect",
        "check-timeout",
        "revocation-failed",
        "late-transfer",
        "late-suspension",
    ],
)
def test_unconfirmed_known_check_never_returns_a_receipt(fault: str) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused) as error:
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            commit_sha=COMMIT,
            check_run_id=91,
            _transport=transport(fault, calls, check=True),
        )
    assert TOKEN not in str(error.value) and "private check" not in str(error.value)
    assert calls[-1].method == "DELETE"
    assert sum(call.method == "POST" for call in calls) == 1


def test_missing_checks_permission_never_issues_a_token() -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused):
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            commit_sha=COMMIT,
            check_run_id=91,
            _transport=transport("missing-checks", calls, check=True),
        )
    assert [call.method for call in calls] == ["GET"]


@pytest.mark.parametrize("check_id,commit", [(True, COMMIT), (0, COMMIT), (91, None)])
def test_malformed_check_scope_makes_no_request(check_id: int, commit: str | None) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused, match="exact check ID"):
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            commit_sha=commit,
            check_run_id=check_id,
            _transport=transport(None, calls, check=True),
        )
    assert not calls


def test_exact_commit_probe_rechecks_repository_and_discards_commit_text() -> None:
    calls: list[httpx.Request] = []
    result = inspect_repository(
        SCOPE,
        app_jwt="a.b.c",
        allow_temporary_token_issuance=True,
        commit_sha=COMMIT,
        _transport=transport(None, calls),
    )
    assert result.commit_sha == COMMIT and result.token_revoked
    assert "untrusted commit text" not in repr(result)
    assert [call.method for call in calls] == ["GET", "POST", "GET", "GET", "GET", "GET", "DELETE"]
    assert calls[3].url.path.endswith("/git/commits/" + COMMIT)


@pytest.mark.parametrize(
    "fault", ["wrong-commit", "missing-commit", "commit-redirect", "late-transfer"]
)
def test_unconfirmed_commit_or_changed_repository_revokes_before_refusing(fault: str) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused):
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            commit_sha=COMMIT,
            _transport=transport(fault, calls),
        )
    assert calls[-1].method == "DELETE"
    assert sum(call.method == "POST" for call in calls) == 1


@pytest.mark.parametrize("commit", ["main", "a" * 39, "A" * 40, "../other", "a" * 40 + " "])
def test_mutable_or_malformed_commit_never_issues_a_token(commit: str) -> None:
    calls: list[httpx.Request] = []
    with pytest.raises(Refused, match="immutable source commit"):
        inspect_repository(
            SCOPE,
            app_jwt="a.b.c",
            allow_temporary_token_issuance=True,
            commit_sha=commit,
            _transport=transport(None, calls),
        )
    assert calls == []


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
