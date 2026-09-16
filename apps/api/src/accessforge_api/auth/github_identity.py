"""GitHub.com identity transport, not a browser-login route or session issuer.

The caller must first consume a persistent, short-lived, browser-bound OAuth state
record and obtain its original PKCE verifier. Never call this with a verifier from
the callback query. The returned numeric subject requires an explicit operator-owned
account binding; email/login are neither requested nor used to link local accounts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

_EXCHANGE_ENDPOINT = "https://github.com/login/oauth/access_token"
_USER = "https://api.github.com/user"
_MAX_RESPONSE = 65536
_DEADLINE = 10.0
_RANDOM = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")


class GitHubIdentityError(Exception):
    """Generic refusal: provider bodies, codes and tokens never enter diagnostics."""


@dataclass(frozen=True, slots=True)
class GitHubOAuthConfiguration:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    def __post_init__(self) -> None:
        uri = urlsplit(self.redirect_uri)
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", self.client_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", self.client_secret)
            or len(self.redirect_uri) > 2048
            or any(character.isspace() or ord(character) < 32 for character in self.redirect_uri)
            or "\\" in self.redirect_uri
            or uri.scheme != "https"
            or not uri.hostname
            or (uri.port is not None and not 1 <= uri.port <= 65535)
            or uri.username is not None
            or uri.password is not None
            or uri.query
            or uri.fragment
            or uri.path != "/v1/auth/github/callback"
        ):
            raise ValueError("invalid GitHub OAuth configuration")


@dataclass(frozen=True, slots=True)
class GitHubSubject:
    user_id: str


def authorization_url(config: GitHubOAuthConfiguration, *, state: str, code_verifier: str) -> str:
    if not _RANDOM.fullmatch(state) or not _RANDOM.fullmatch(code_verifier):
        raise GitHubIdentityError("invalid authorization request")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
    return "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "state": state,
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "scope": "",
            "allow_signup": "false",
        }
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider field")
        result[key] = value
    return result


async def _json_response(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code != 200:
            raise GitHubIdentityError("GitHub identity exchange refused")
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise GitHubIdentityError("GitHub identity exchange refused")
        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise GitHubIdentityError("GitHub identity exchange refused")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=8192):
            if len(body) + len(chunk) > _MAX_RESPONSE:
                raise GitHubIdentityError("GitHub identity exchange refused")
            body.extend(chunk)
        value = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise GitHubIdentityError("GitHub identity exchange refused")
        return value


async def exchange_identity(
    config: GitHubOAuthConfiguration,
    *,
    code: str,
    code_verifier: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GitHubSubject:
    """One exchange, one fresh identity read, no retries, redirects or session issuance.

    transport is a trusted construction-time test port, never request configuration.
    The full operation has one cancellation deadline, including response-body reads.
    Use a dedicated zero-scope OAuth application, not the publication GitHub App.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", code) or not _RANDOM.fullmatch(code_verifier):
        raise GitHubIdentityError("GitHub identity exchange refused")
    try:
        async with asyncio.timeout(_DEADLINE):
            async with httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=5.0,
                transport=transport,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "AccessForge-Identity",
                },
            ) as client:
                reply = await _json_response(
                    client,
                    "POST",
                    _EXCHANGE_ENDPOINT,
                    data={
                        "client_id": config.client_id,
                        "client_secret": config.client_secret,
                        "redirect_uri": config.redirect_uri,
                        "code": code,
                        "code_verifier": code_verifier,
                    },
                )
                token = reply.get("access_token")
                if (
                    "error" in reply
                    or reply.get("token_type") != "bearer"
                    or reply.get("scope") != ""
                    or not isinstance(token, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", token)
                ):
                    raise GitHubIdentityError("GitHub identity exchange refused")
                identity = await _json_response(
                    client,
                    "GET",
                    _USER,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                subject = identity.get("id")
                if type(subject) is not int or not 0 < subject < 2**63:
                    raise GitHubIdentityError("GitHub identity exchange refused")
                return GitHubSubject(user_id=str(subject))
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError, GitHubIdentityError):
        raise GitHubIdentityError("GitHub identity exchange refused") from None
