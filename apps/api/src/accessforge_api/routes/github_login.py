"""Optional GitHub browser identity; no subject supplied by the browser is trusted."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, Request
from psycopg.conninfo import make_conninfo
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from accessforge_api.auth.github_accounts import issue_github_session
from accessforge_api.auth.github_challenges import (
    GitHubChallengeRateLimited,
    consume_challenge,
    create_challenge,
)
from accessforge_api.auth.github_identity import (
    GitHubIdentityError,
    GitHubOAuthConfiguration,
    authorization_url,
    exchange_identity,
)
from accessforge_api.auth.membership import record_global_audit_event
from accessforge_api.config import ApiSettings
from accessforge_api.problems import ProblemCode, ProblemDetail
from accessforge_persistence import unscoped_connection

from .session import _set_session_cookies

router = APIRouter(prefix="/v1/auth/github", tags=["session"])
LOGIN_COOKIE = "__Host-accessforge_github_login"
LOGIN_PATHS = frozenset({"/v1/auth/github/start", "/v1/auth/github/callback"})


def _database_url(settings: ApiSettings) -> str:
    # Override even a longer operator DSN timeout on this unauthenticated path.
    # The returned conninfo contains credentials and must never be logged.
    return make_conninfo(settings.database_url, connect_timeout=5)


def protect_response(response: Response, *, callback: bool) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if callback:
        response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def _configuration(request: Request) -> tuple[ApiSettings, GitHubOAuthConfiguration]:
    settings: ApiSettings = request.app.state.config
    if settings.identity_provider != "github":
        raise ProblemDetail(ProblemCode.DEPENDENCY_UNAVAILABLE, "GitHub browser login is disabled")
    if len(request.scope.get("query_string", b"")) > 4096:
        raise GitHubIdentityError("GitHub login refused")
    config = settings.github_identity_configuration()
    expected, actual = urlsplit(config.redirect_uri), urlsplit(str(request.url))
    if (actual.scheme, actual.hostname, actual.port or 443) != (
        "https",
        expected.hostname,
        expected.port or 443,
    ):
        raise GitHubIdentityError("GitHub login refused")
    return settings, config


def _denial(database_url: str) -> None:
    # Fixed metadata only: never code, state, cookie, provider error text or query IDs.
    with unscoped_connection(database_url) as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        record_global_audit_event(
            conn,
            action="session.github_denied",
            target_kind="session",
            target_id=None,
            outcome="DENIED",
            actor_service="github-identity",
        )


async def _refuse(request: Request, *, unavailable: bool = False) -> None:
    try:
        await run_in_threadpool(_denial, _database_url(request.app.state.config))
    except psycopg.Error:
        unavailable = True
    raise ProblemDetail(
        ProblemCode.DEPENDENCY_UNAVAILABLE if unavailable else ProblemCode.NOT_AUTHENTICATED,
        "GitHub browser login unavailable" if unavailable else "GitHub browser login refused",
    )


def _browser_secrets(request: Request) -> tuple[str, str]:
    values = []
    headers = request.headers.getlist("cookie")
    if sum(map(len, headers)) > 16384:
        raise GitHubIdentityError("GitHub login refused")
    for header in headers:
        for item in header.split(";"):
            name, separator, value = item.strip().partition("=")
            if name == LOGIN_COOKIE and separator:
                values.append(value)
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}", values[0]):
        raise GitHubIdentityError("GitHub login refused")
    browser, verifier = values[0].split(".")
    return browser, verifier


@router.get(
    "/start",
    status_code=303,
    response_class=RedirectResponse,
    openapi_extra={"x-accessforge-browser-navigation": True},
)
async def github_login_start(request: Request) -> Response:
    """Top-level browser navigation, not a fetch/JSON API. Redirects to GitHub.com."""
    try:
        settings, config = _configuration(request)
        expected = urlsplit(config.redirect_uri)
        origin = f"https://{expected.netloc}"
        if (
            request.query_params
            or request.headers.get("sec-fetch-site") == "cross-site"
            or request.headers.get("origin", origin) != origin
        ):
            raise GitHubIdentityError("GitHub login refused")
        challenge = await run_in_threadpool(create_challenge, _database_url(settings), config)
        response = RedirectResponse(
            authorization_url(config, state=challenge.state, code_verifier=challenge.code_verifier),
            status_code=303,
        )
        response.set_cookie(
            LOGIN_COOKIE,
            f"{challenge.browser_secret}.{challenge.code_verifier}",
            max_age=300,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response
    except GitHubChallengeRateLimited:
        raise ProblemDetail(
            ProblemCode.RATE_LIMITED,
            "GitHub login admission limited",
            extra={"retryAfterSeconds": 60},
            headers={"Retry-After": "60"},
        ) from None
    except GitHubIdentityError:
        await _refuse(request)
    except (psycopg.Error, ValueError):
        await _refuse(request, unavailable=True)
    raise AssertionError("unreachable")


@router.get(
    "/callback",
    status_code=303,
    response_class=RedirectResponse,
    openapi_extra={"x-accessforge-browser-navigation": True},
)
async def github_login_callback(request: Request) -> Response:
    """Consume browser state before exchange; redirect only to the fixed local root."""
    try:
        settings, config = _configuration(request)
        pairs = list(request.query_params.multi_items())
        if len(request.scope.get("query_string", b"")) > 4096 or len(
            {key for key, _ in pairs}
        ) != len(pairs):
            raise GitHubIdentityError("GitHub login refused")
        query = dict(pairs)
        if (
            set(query) - {"state", "code", "error", "error_description", "error_uri", "iss"}
            # GitHub emits RFC 9207 issuer identification. Accept its exact
            # documented issuer, never arbitrary callback-supplied providers.
            or ("iss" in query and query["iss"] != "https://github.com/login/oauth")
            or "state" not in query
            or ("code" in query) == ("error" in query)
        ):
            raise GitHubIdentityError("GitHub login refused")
        browser, verifier = _browser_secrets(request)
        await run_in_threadpool(
            consume_challenge,
            _database_url(settings),
            config,
            state=query["state"],
            browser_secret=browser,
            code_verifier=verifier,
        )
        if "error" in query:
            raise GitHubIdentityError("GitHub login refused")
        identity = await exchange_identity(config, code=query["code"], code_verifier=verifier)
        issued = await run_in_threadpool(issue_github_session, _database_url(settings), identity)
        response = RedirectResponse("/", status_code=303)
        _set_session_cookies(
            response,
            session_token=issued.session_token,
            csrf_token=issued.csrf_token,
            secure=True,
            expires=issued.expires_at,
        )
        return response
    except GitHubIdentityError:
        await _refuse(request)
    except (psycopg.Error, ValueError):
        await _refuse(request, unavailable=True)
    raise AssertionError("unreachable")
