"""Same-origin build serving with no DB, server process, model or reader execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.config import ApiSettings
from accessforge_api.static_web import StaticWeb


def config(web: Path | None = None) -> ApiSettings:
    return ApiSettings(
        database_url="postgresql://unused:unused@localhost/unused",
        evidence_endpoint_url="http://localhost:9000",
        evidence_bucket="unused",
        evidence_access_key="test-access-key",
        evidence_secret_key="test-secret-key",
        identity_provider="none",
        web_dist_directory=str(web) if web else None,
    )


@pytest.fixture
def web(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / "web"
    root.mkdir()
    (root / "index.html").write_text("<html>AccessForge test shell</html>")
    (root / "assets").mkdir()
    (root / "assets/app.js").write_text("void 0;")
    (root / "assets/app.js.map").write_text("private source map")
    (root / ".env").write_text("private test sentinel")
    return root


def test_ui_and_assets_share_origin_without_shadowing_api(web: Path) -> None:
    app = create_app(config(web))
    client = TestClient(app)  # No lifespan context: no database readiness/startup.
    for path in ("/", "/workspaces", "/w/test-workspace/runs/run-1"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "AccessForge test shell" in response.text
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "void 0;"
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.post("/v1/sessions", json={"email": "unknown@example.invalid"}).status_code == 503
    assert "/{path}" not in app.openapi()["paths"]
    head = client.head("/assets/app.js")
    assert head.content == b""
    assert head.headers["content-length"] == str(len(asset.content))
    cached = client.get("/assets/app.js", headers={"If-None-Match": asset.headers["etag"]})
    assert cached.status_code == 304
    assert cached.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/assets/app.js.map",
        "/assets/missing.js",
        "/v1/missing",
        "/health/missing",
        "/w/secret.txt",
        "/assets/%2e%2e/.env",
    ],
)
def test_private_and_unknown_paths_are_not_html_success(web: Path, path: str) -> None:
    response = TestClient(create_app(config(web))).get(path)
    assert response.status_code == 404
    assert "private" not in response.text
    assert "AccessForge test shell" not in response.text


def test_serving_is_optional_and_contract_is_unchanged(web: Path) -> None:
    api_only = create_app(config())
    assert TestClient(api_only).get("/").status_code == 404
    assert api_only.openapi() == create_app(config(web)).openapi()


def test_snapshot_does_not_follow_later_file_replacement(web: Path) -> None:
    client = TestClient(create_app(config(web)))
    (web / "assets/app.js").unlink()
    (web / "assets/app.js").symlink_to(web / ".env")
    assert client.get("/assets/app.js").text == "void 0;"
    with pytest.raises(ValueError, match="symlink"):
        StaticWeb(web)


def test_missing_or_noncanonical_build_refuses_startup(web: Path) -> None:
    with pytest.raises(ValueError):
        StaticWeb(Path("relative/build"))
    (web / "index.html").unlink()
    with pytest.raises(ValueError):
        create_app(config(web))
