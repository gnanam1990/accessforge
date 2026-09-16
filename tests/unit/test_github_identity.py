"""Synthetic GitHub replies only: no provider calls, sessions, browser or reader."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from accessforge_api.auth import github_identity as identity

CONFIG = identity.GitHubOAuthConfiguration(
    client_id="example-client",
    client_secret="private-test-client-secret",
    redirect_uri="https://accessforge.example/v1/auth/github/callback",
)
VERIFIER = "v" * 43
TOKEN = {"access_token": "private-test-provider-token", "token_type": "bearer", "scope": ""}


def test_authorization_is_fixed_pkce_zero_scope_and_contains_no_secret() -> None:
    url = identity.authorization_url(CONFIG, state="s" * 43, code_verifier=VERIFIER)
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https",
        "github.com",
        "/login/oauth/authorize",
    )
    query = parse_qs(parts.query, keep_blank_values=True)
    assert query["scope"] == [""] and query["code_challenge_method"] == ["S256"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    )
    assert query["code_challenge"] == [expected]
    assert VERIFIER not in url and CONFIG.client_secret not in url
    assert CONFIG.client_secret not in repr(CONFIG)


@pytest.mark.parametrize(
    "uri",
    [
        "http://accessforge.example/v1/auth/github/callback",
        "https://user:secret@accessforge.example/v1/auth/github/callback",
        "https://accessforge.example/v1/auth/github/callback?redirect=evil",
        "https://accessforge.example/v1/auth/github/callback#fragment",
        "https://accessforge.example/other",
        "https://accessforge.example\n/v1/auth/github/callback",
    ],
)
def test_callback_configuration_is_closed(uri: str) -> None:
    with pytest.raises(ValueError):
        identity.GitHubOAuthConfiguration("client", "s" * 32, uri)


@pytest.mark.asyncio
async def test_exchange_revalidates_numeric_identity_and_drops_profile_and_tokens() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            assert str(request.url) == "https://github.com/login/oauth/access_token"
            assert request.method == "POST" and "authorization" not in request.headers
            fields = parse_qs(request.content.decode())
            assert fields["code_verifier"] == [VERIFIER]
            assert fields["redirect_uri"] == [CONFIG.redirect_uri]
            return httpx.Response(200, json=TOKEN)
        assert str(request.url) == "https://api.github.com/user"
        assert request.headers["authorization"] == "Bearer " + str(TOKEN["access_token"])
        assert request.method == "GET" and not request.content
        return httpx.Response(200, json={"id": 123, "login": "mutable", "email": "untrusted"})

    result = await identity.exchange_identity(
        CONFIG, code="code", code_verifier=VERIFIER, transport=httpx.MockTransport(respond)
    )
    assert result == identity.GitHubSubject("123") and len(calls) == 2
    assert "mutable" not in repr(result) and "token" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {},
        {**TOKEN, "scope": "repo"},
        {**TOKEN, "token_type": "basic"},
        {**TOKEN, "access_token": "bad\r\nheader"},
        {**TOKEN, "error": "secret-detail"},
    ],
)
async def test_bad_token_reply_never_reaches_identity_endpoint(reply: dict[str, object]) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=reply)

    with pytest.raises(identity.GitHubIdentityError, match="^GitHub identity exchange refused$"):
        await identity.exchange_identity(
            CONFIG, code="code", code_verifier=VERIFIER, transport=httpx.MockTransport(respond)
        )
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, True, 0, -1, "123", 2**63])
async def test_missing_or_ambiguous_subject_is_refused(subject: object) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=TOKEN if request.method == "POST" else {"id": subject})

    with pytest.raises(identity.GitHubIdentityError):
        await identity.exchange_identity(
            CONFIG, code="code", code_verifier=VERIFIER, transport=httpx.MockTransport(respond)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["redirect", "duplicate", "oversized", "html", "invalid-json"])
async def test_bad_http_response_is_not_retried_or_followed(kind: str) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = {
            "duplicate": b'{"id":1,"id":2}',
            "oversized": b"x" * 65537,
            "html": b"<html>private-error</html>",
            "invalid-json": b"not-json",
        }.get(kind, b"")
        return httpx.Response(
            302 if kind == "redirect" else 200,
            content=body,
            headers={
                "content-type": "text/html" if kind == "html" else "application/json",
                "location": "https://elsewhere.example/secret-sink",
            },
        )

    with pytest.raises(identity.GitHubIdentityError, match="^GitHub identity exchange refused$"):
        await identity.exchange_identity(
            CONFIG, code="code", code_verifier=VERIFIER, transport=httpx.MockTransport(respond)
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_total_deadline_closes_slow_body_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{"
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(identity, "_DEADLINE", 0.02)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=SlowBody()
        )
    )
    with pytest.raises(identity.GitHubIdentityError):
        await identity.exchange_identity(
            CONFIG, code="code", code_verifier=VERIFIER, transport=transport
        )
    assert closed


@pytest.mark.asyncio
async def test_compressed_body_is_closed_before_reading_or_decompression() -> None:
    read = closed = False

    class EncodedBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            nonlocal read
            read = True
            yield b"never-decode-this"

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            stream=EncodedBody(),
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        )

    with pytest.raises(identity.GitHubIdentityError):
        await identity.exchange_identity(
            CONFIG, code="code", code_verifier=VERIFIER, transport=httpx.MockTransport(respond)
        )
    assert closed and not read
