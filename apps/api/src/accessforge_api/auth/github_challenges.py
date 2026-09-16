"""Durable pre-authentication state; no routes, provider calls or account issuance.

The browser route must set browser_secret and code_verifier in an HttpOnly, Secure,
SameSite=Lax cookie. Only state goes in the authorization query. Never accept a
verifier or browser secret from callback query parameters. Each helper owns its
connection: successful consumption is committed before control returns, so a
later failed provider exchange cannot roll it back. A lost commit acknowledgement
must fail closed, not trigger an exchange or automatic retry.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field

from accessforge_persistence import unscoped_connection

from .github_identity import GitHubIdentityError, GitHubOAuthConfiguration

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class GitHubChallengeRateLimited(GitHubIdentityError):
    """Global admission capacity is exhausted; no provider request is authorized."""


@dataclass(frozen=True, slots=True)
class BrowserChallenge:
    state: str = field(repr=False)
    browser_secret: str = field(repr=False)
    code_verifier: str = field(repr=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _configuration(config: GitHubOAuthConfiguration) -> str:
    # Bind secret rotation too. A digest of a high-entropy client secret is not
    # a reusable provider credential; never persist/log the canonical input.
    return _hash(
        json.dumps(
            [
                "accessforge.github-login/1",
                config.client_id,
                config.client_secret,
                config.redirect_uri,
            ],
            separators=(",", ":"),
        )
    )


def create_challenge(database_url: str, config: GitHubOAuthConfiguration) -> BrowserChallenge:
    """Commit a five-minute challenge before returning its transient credentials."""
    challenge = BrowserChallenge(*(secrets.token_urlsafe(32) for _ in range(3)))
    limited = False
    with unscoped_connection(database_url) as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        # One database-wide admission lock across replicas. Nonblocking: never
        # accumulate waiting login creation transactions. Not an IP fairness policy.
        lock = conn.execute("SELECT pg_try_advisory_xact_lock(713021, 1) AS acquired").fetchone()
        if lock is None or not lock["acquired"]:
            raise GitHubChallengeRateLimited("GitHub login admission limited")
        conn.execute(
            "DELETE FROM github_login_challenge WHERE state_hash IN "
            "(SELECT state_hash FROM github_login_challenge WHERE expires_at <= clock_timestamp() "
            "ORDER BY expires_at LIMIT 1000)"
        )
        counts = conn.execute(
            "SELECT count(*) AS retained, count(*) FILTER "
            "(WHERE created_at > clock_timestamp() - interval '1 minute') AS recent "
            "FROM github_login_challenge"
        ).fetchone()
        limited = counts is None or counts["retained"] >= 1000 or counts["recent"] >= 120
        if not limited:
            conn.execute(
                """
            WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at)
            INSERT INTO github_login_challenge
                (state_hash, browser_hash, verifier_hash, configuration_hash,
                 created_at, expires_at)
            SELECT %s, %s, %s, %s, at, at + interval '5 minutes' FROM moment
            """,
                (
                    _hash(challenge.state),
                    _hash(challenge.browser_secret),
                    _hash(challenge.code_verifier),
                    _configuration(config),
                ),
            )
    # Commit expired-row cleanup even when creation is refused.
    if limited:
        raise GitHubChallengeRateLimited("GitHub login admission limited")
    return challenge


def consume_challenge(
    database_url: str,
    config: GitHubOAuthConfiguration,
    *,
    state: str,
    browser_secret: str,
    code_verifier: str,
) -> None:
    """Atomically consume and commit before returning, or refuse generically.

    All replicas use the same database predicate and row lock. Wrong browser or
    configuration attempts cannot burn a legitimate challenge. This does not
    authenticate a user: the caller still needs fresh provider identity and an
    independently trusted local-account binding.
    """
    if not all(_TOKEN.fullmatch(value) for value in (state, browser_secret, code_verifier)):
        raise GitHubIdentityError("GitHub login challenge refused")
    with unscoped_connection(database_url) as conn:
        # Bound contention; failures propagate and must never authorize exchange.
        conn.execute("SET LOCAL statement_timeout = '5s'")
        # Acquire the lock first, then evaluate expiry with a fresh statement.
        # A predicate evaluated before a lock wait must not extend the lifetime.
        conn.execute(
            "SELECT state_hash FROM github_login_challenge WHERE state_hash = %s FOR UPDATE",
            (_hash(state),),
        ).fetchone()
        row = conn.execute(
            """
            UPDATE github_login_challenge SET consumed_at = clock_timestamp()
            WHERE state_hash = %s AND browser_hash = %s AND verifier_hash = %s
              AND configuration_hash = %s AND consumed_at IS NULL
              AND expires_at > clock_timestamp()
            RETURNING state_hash
            """,
            (_hash(state), _hash(browser_secret), _hash(code_verifier), _configuration(config)),
        ).fetchone()
        if row is None:
            raise GitHubIdentityError("GitHub login challenge refused")
