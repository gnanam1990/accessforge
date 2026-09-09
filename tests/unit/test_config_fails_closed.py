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
