"""Invalid configuration must fail closed, and diagnostics must never leak secrets."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from accessforge_api.config import ApiSettings
from reference_app.config import ReferenceAppSettings

REFAPP_VALID = {
    "database_url": "postgresql://user:pw@localhost:5432/refapp",
    "observer_token": "o" * 32,
    "setup_token": "s" * 32,
}
API_VALID = {
    "database_url": "postgresql://user:pw@localhost:5432/accessforge",
    "evidence_endpoint_url": "http://localhost:9000",
    "evidence_bucket": "bucket",
    "evidence_access_key": "access-key-value",
    "evidence_secret_key": "secret-key-value",
}


def test_valid_configuration_is_accepted() -> None:
    # Allowed-path control: an always-reject validator must fail this test.
    assert ReferenceAppSettings(**REFAPP_VALID).port == 8081
    assert ApiSettings(**API_VALID).environment == "local"


# S104 flags the all-interfaces literal; this test exists precisely to prove it is rejected.
@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com", "::"])  # noqa: S104
def test_reference_app_refuses_non_loopback_binding(host: str) -> None:
    with pytest.raises(ValidationError, match="non-loopback"):
        ReferenceAppSettings(**REFAPP_VALID, host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_reference_app_accepts_loopback(host: str) -> None:
    assert ReferenceAppSettings(**REFAPP_VALID, host=host).host == host


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@db.production.example.com:5432/app",
        "postgresql://user:pw@10.0.0.5:5432/app",
        "sqlite:///local.db",
    ],
)
def test_reference_app_refuses_non_local_database(url: str) -> None:
    with pytest.raises(ValidationError):
        ReferenceAppSettings(**{**REFAPP_VALID, "database_url": url})


@pytest.mark.parametrize(
    "token",
    [
        "changeme_changeme_changeme",
        "PLACEHOLDER_VALUE_HERE_XX",
        # The exact value our own .env.refapp.example ships. An equality-only check would
        # have accepted it, so the fixture would run with a guessable observer token.
        "REPLACE_ME_WITH_A_RANDOM_VALUE",
        "your_token_goes_here_here",
    ],
)
def test_placeholder_tokens_are_refused(token: str) -> None:
    with pytest.raises(ValidationError):
        ReferenceAppSettings(**{**REFAPP_VALID, "observer_token": token})


def test_example_file_placeholders_are_refused_for_credentials() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(**{**API_VALID, "evidence_secret_key": "REPLACE_ME"})


def test_short_tokens_are_refused() -> None:
    with pytest.raises(ValidationError):
        ReferenceAppSettings(**{**REFAPP_VALID, "setup_token": "tooshort"})


def test_unknown_configuration_keys_are_refused() -> None:
    # extra="forbid": a typo in an env var must not silently leave a default in place.
    with pytest.raises(ValidationError):
        ReferenceAppSettings(**REFAPP_VALID, unexpected_option="x")


def test_missing_required_configuration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # The ambient environment supplies these in development, which would mask the failure this
    # test exists to prove. Clear them and point the dotenv source at nothing.
    for key in list(os.environ):
        if key.startswith("ACCESSFORGE_"):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError) as excinfo:
        ApiSettings(  # type: ignore[call-arg]
            _env_file=None, database_url="postgresql://u:p@localhost/db"
        )
    missing = {e["loc"][0] for e in excinfo.value.errors()}
    assert "evidence_endpoint_url" in missing
    assert "evidence_secret_key" in missing


def test_secrets_never_appear_in_diagnostics() -> None:
    refapp = ReferenceAppSettings(**REFAPP_VALID)
    api = ApiSettings(**API_VALID)
    rendered = repr(refapp.redacted()) + repr(api.redacted())
    for secret in ("o" * 32, "s" * 32, "pw", "access-key-value", "secret-key-value"):
        assert secret not in rendered, f"{secret!r} leaked into diagnostics"
    assert "<redacted>" in rendered


# --- findings from independent review of module 01 -----------------------------------------
# Each of these reproduces a confirmed bypass and must fail before the corresponding fix.


@pytest.mark.parametrize(
    "url",
    [
        # The marker substring appears outside the authority, so a substring check accepts a
        # URL whose real host is remote.
        "postgresql://user:pass@evil.example.com:5432/refapp?application_name=x@localhost",
        "postgresql://user:pass@evil.example.com:5432/db?options=-c%20search_path=@127.0.0.1",
        "postgresql://user:pass@evil.example.com:5432/@localhost",
        # A query parameter can override the authority host entirely.
        "postgresql://localhost:5432/db?host=evil.example.com",
    ],
)
def test_non_local_database_cannot_be_smuggled_past_the_guard(url: str) -> None:
    with pytest.raises(ValidationError):
        ReferenceAppSettings(**{**REFAPP_VALID, "database_url": url})


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@localhost:5432/refapp",
        "postgresql://user:pw@127.0.0.1:5432/refapp",
        "postgresql://user:pw@[::1]:5432/refapp",
        "postgresql:///refapp",  # local unix socket
        "postgresql://user@/refapp",
    ],
)
def test_genuinely_local_databases_are_still_accepted(url: str) -> None:
    # Allowed-path control: a guard that rejected everything would fail here.
    assert ReferenceAppSettings(**{**REFAPP_VALID, "database_url": url}).database_url == url


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_non_tls_evidence_endpoint_is_refused_outside_local(environment: str) -> None:
    # Object-store credentials must not cross the network in plaintext.
    with pytest.raises(ValidationError, match="https"):
        ApiSettings(
            **{
                **API_VALID,
                "environment": environment,
                "evidence_endpoint_url": "http://evidence.example.com",
            }
        )


def test_production_refuses_a_loopback_database() -> None:
    # A production deployment pointing at its own loopback is a misconfiguration, not a choice.
    with pytest.raises(ValidationError, match="loopback"):
        ApiSettings(
            **{
                **API_VALID,
                "environment": "production",
                "evidence_endpoint_url": "https://evidence.example.com",
                "database_url": "postgresql://u:p@localhost:5432/db",
            }
        )


def test_local_development_is_unaffected_by_production_rules() -> None:
    # Allowed-path control for the environment-conditional rules.
    s = ApiSettings(**API_VALID)  # environment defaults to "local"
    assert s.environment == "local"
    assert s.evidence_endpoint_url.startswith("http://")
