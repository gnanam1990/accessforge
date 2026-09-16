"""Real PostgreSQL challenge durability; no browser, reader or provider calls."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from time import monotonic, sleep

import pytest

from accessforge_api.auth.github_challenges import (
    BrowserChallenge,
    consume_challenge,
    create_challenge,
)
from accessforge_api.auth.github_identity import GitHubIdentityError, GitHubOAuthConfiguration
from accessforge_persistence import migrate, unscoped_connection

pytestmark = pytest.mark.integration

CONFIG = GitHubOAuthConfiguration(
    "test-client", "x" * 32, "https://example.test/v1/auth/github/callback"
)


@pytest.fixture()
def db(test_database_url: str) -> str:
    migrate(test_database_url)
    return test_database_url


def _consume(
    db: str, challenge: BrowserChallenge, config: GitHubOAuthConfiguration = CONFIG
) -> None:
    consume_challenge(
        db,
        config,
        state=challenge.state,
        browser_secret=challenge.browser_secret,
        code_verifier=challenge.code_verifier,
    )


def test_only_hashes_persist_and_consumption_survives_caller_rollback(db: str) -> None:
    challenge = create_challenge(db, CONFIG)
    with unscoped_connection(db) as conn:
        row = conn.execute(
            "SELECT * FROM github_login_challenge WHERE state_hash = %s",
            (hashlib.sha256(challenge.state.encode()).hexdigest(),),
        ).fetchone()
    assert row is not None
    assert (row["expires_at"] - row["created_at"]).total_seconds() == 300
    for token in (challenge.state, challenge.browser_secret, challenge.code_verifier):
        assert token not in repr(row)
        assert token not in repr(challenge)
    with pytest.raises(RuntimeError, match="exchange failed"):
        with unscoped_connection(db):
            _consume(db, challenge)
            raise RuntimeError("exchange failed")
    with pytest.raises(GitHubIdentityError, match="challenge refused"):
        _consume(db, challenge)


@pytest.mark.parametrize("field", ["state", "browser_secret", "code_verifier"])
def test_wrong_browser_credentials_do_not_burn_the_challenge(db: str, field: str) -> None:
    challenge = create_challenge(db, CONFIG)
    with pytest.raises(GitHubIdentityError):
        _consume(db, replace(challenge, **{field: "z" * 43}))
    _consume(db, challenge)


@pytest.mark.parametrize(
    "change",
    [
        {"client_id": "another-client"},
        {"client_secret": "y" * 32},
        {"redirect_uri": "https://another.test/v1/auth/github/callback"},
    ],
)
def test_configuration_drift_refuses_without_consumption(db: str, change: dict[str, str]) -> None:
    challenge = create_challenge(db, CONFIG)
    with pytest.raises(GitHubIdentityError):
        _consume(db, challenge, replace(CONFIG, **change))
    _consume(db, challenge)


def test_two_replicas_cannot_both_consume(db: str) -> None:
    challenge = create_challenge(db, CONFIG)
    barrier = Barrier(2)

    def attempt() -> bool:
        barrier.wait(timeout=5)
        try:
            _consume(db, challenge)
        except GitHubIdentityError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [False, True]


def test_expiry_is_checked_after_a_row_lock_wait(db: str) -> None:
    challenge = create_challenge(db, CONFIG)
    state_hash = hashlib.sha256(challenge.state.encode()).hexdigest()
    with unscoped_connection(db) as conn:
        conn.execute(
            "UPDATE github_login_challenge SET expires_at = clock_timestamp() "
            "+ interval '2 seconds' WHERE state_hash = %s",
            (state_hash,),
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with unscoped_connection(db) as conn:
            conn.execute(
                "SELECT state_hash FROM github_login_challenge WHERE state_hash = %s FOR UPDATE",
                (state_hash,),
            )
            future = pool.submit(_consume, db, challenge)
            deadline = monotonic() + 1.5
            while True:
                # Refresh the statistics snapshot inside this open transaction.
                conn.execute("SELECT pg_stat_clear_snapshot()")
                waiting = conn.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                    "AND query LIKE 'SELECT state_hash FROM github_login_challenge%FOR UPDATE'"
                ).fetchone()
                if waiting is not None:
                    break
                assert monotonic() < deadline, "consumer never reached the held row lock"
                sleep(0.01)
            live = conn.execute(
                "SELECT expires_at > clock_timestamp() AS live FROM github_login_challenge "
                "WHERE state_hash = %s",
                (state_hash,),
            ).fetchone()
            assert live is not None and live["live"]
            conn.execute("SELECT pg_sleep(2.1)")
        with pytest.raises(GitHubIdentityError):
            future.result(timeout=10)
    with unscoped_connection(db) as conn:
        row = conn.execute(
            "SELECT consumed_at FROM github_login_challenge WHERE state_hash = %s", (state_hash,)
        ).fetchone()
    assert row is not None and row["consumed_at"] is None
