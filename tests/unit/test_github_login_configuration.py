"""Disabled-by-default identity configuration, browser-only contract and safe server logs."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from accessforge_api import __main__ as entrypoint
from accessforge_api.app import create_app
from accessforge_api.config import ApiSettings


def _settings(**changes: Any) -> ApiSettings:
    values: dict[str, Any] = dict(
        _env_file=None,
        database_url="postgresql://unused@127.0.0.1/unused",
        environment="local",
        evidence_endpoint_url="http://127.0.0.1:9000",
        evidence_bucket="unused",
        evidence_access_key="test-key-value",
        evidence_secret_key="test-key-value",
        identity_provider="github",
        github_oauth_client_id="test-client",
        github_oauth_client_secret="a" * 32,
        github_oauth_redirect_uri="https://app.example.test/v1/auth/github/callback",
    )
    values.update(changes)
    return ApiSettings(**values)


@pytest.mark.parametrize(
    "field", ["github_oauth_client_id", "github_oauth_client_secret", "github_oauth_redirect_uri"]
)
def test_partial_configuration_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: None})


@pytest.mark.parametrize(
    "uri",
    [
        "http://app.example.test/v1/auth/github/callback",
        "https://app.example.test:0/v1/auth/github/callback",
        "https://app.example.test:bad/v1/auth/github/callback",
        "https://app.example.test:65536/v1/auth/github/callback",
    ],
)
def test_insecure_or_invalid_callback_origin_is_refused(uri: str) -> None:
    with pytest.raises(ValidationError):
        _settings(github_oauth_redirect_uri=uri)


@pytest.mark.parametrize("provider", ["none", "local-development"])
def test_disabled_provider_cannot_silently_retain_configured_credentials(provider: str) -> None:
    with pytest.raises(ValidationError):
        _settings(identity_provider=provider)


def test_secret_is_not_in_config_repr_or_diagnostics() -> None:
    settings = _settings()
    assert "a" * 32 not in repr(settings) + repr(settings.redacted())


def test_browser_redirects_are_public_but_not_json_client_operations() -> None:
    from accessforge_client._operations import OPERATIONS, PATHS

    schema = create_app(_settings()).openapi()
    for path in ("/v1/auth/github/start", "/v1/auth/github/callback"):
        operation = schema["paths"][path]["get"]
        assert operation["security"] == []
        assert operation["x-accessforge-browser-navigation"]
        assert "303" in operation["responses"]
        assert operation["operationId"] not in OPERATIONS
        assert path in PATHS
    assert (
        "Retry-After"
        in schema["paths"]["/v1/auth/github/start"]["get"]["responses"]["429"]["headers"]
    )


def test_server_entrypoint_disables_raw_query_access_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(entrypoint, "ApiSettings", _settings)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    entrypoint.main()
    assert calls[0]["access_log"] is False


@pytest.mark.parametrize("provider", ["none", "local-development", "github"])
def test_public_provider_discovery_contains_no_redirect_credentials_or_identity(
    provider: str,
) -> None:
    changes: dict[str, Any] = {"identity_provider": provider}
    if provider != "github":
        changes.update(
            github_oauth_client_id=None,
            github_oauth_client_secret=None,
            github_oauth_redirect_uri=None,
        )
    app = create_app(_settings(**changes))
    # No lifespan/database is needed: discovery reads only validated configuration.
    client = TestClient(app)
    response = client.get("/v1/auth/options")
    assert response.status_code == 200 and response.json() == {"provider": provider}
    assert response.headers["cache-control"] == "no-store"
    assert app.openapi()["paths"]["/v1/auth/options"]["get"]["security"] == []
