from dataclasses import replace

import pytest
from starlette.requests import Request

from accessforge_api.auth.github_challenges import _configuration
from accessforge_api.auth.github_identity import GitHubIdentityError, GitHubOAuthConfiguration
from accessforge_api.auth.invitation_continuation import InvitationContinuation
from accessforge_api.routes.github_login import LOGIN_COOKIE, _browser_secrets, _continuation

WORKSPACE = "11111111-1111-4111-8111-111111111111"
INVITATION = "22222222-2222-4222-8222-222222222222"
CONFIG = GitHubOAuthConfiguration("test", "x" * 32, "https://example.test/v1/auth/github/callback")


def request(query: str = "", cookie: str = "") -> Request:
    return Request(
        {"type": "http", "query_string": query.encode(), "headers": [(b"cookie", cookie.encode())]}
    )


def test_reference_round_trip_and_configuration_binding() -> None:
    context = InvitationContinuation(WORKSPACE, INVITATION)
    query = context.return_path.split("?", 1)[1]
    assert _continuation(request(query)) == context
    cookie = f"{LOGIN_COOKIE}={'a' * 43}.{'b' * 43}{context.cookie_suffix}"
    assert _browser_secrets(request(cookie=cookie)) == ("a" * 43, "b" * 43, context)
    assert _configuration(CONFIG, context) != _configuration(CONFIG)
    assert _configuration(CONFIG, context) != _configuration(
        CONFIG, replace(context, invitation_id=WORKSPACE)
    )
    assert context.return_path.startswith("/workspaces?")


@pytest.mark.parametrize(
    "query",
    [
        f"invitationWorkspace={WORKSPACE}",
        f"invitationWorkspace={WORKSPACE}&invitationId={INVITATION}&next=https://evil.test",
        f"invitationWorkspace={WORKSPACE}&invitationWorkspace={INVITATION}",
        f"invitationWorkspace={WORKSPACE}&invitationId=//evil.test",
        f"invitationWorkspace={WORKSPACE}&invitationId={{{INVITATION}}}",
    ],
)
def test_invalid_or_open_redirect_context_refused(query: str) -> None:
    with pytest.raises(GitHubIdentityError):
        _continuation(request(query))


@pytest.mark.parametrize("suffix", [".extra", f".{WORKSPACE}", f".{WORKSPACE}.{INVITATION}.extra"])
def test_malformed_cookie_context_refused(suffix: str) -> None:
    with pytest.raises(GitHubIdentityError):
        _browser_secrets(request(cookie=f"{LOGIN_COOKIE}={'a' * 43}.{'b' * 43}{suffix}"))
