"""Trusted operator wiring; no network, actual credentials or application database."""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from accessforge_orchestrator import github_operator as operator
from accessforge_orchestrator.github_access import CreatedCheck
from accessforge_orchestrator.github_app_jwt import AppJWT
from accessforge_orchestrator.maintenance import purge_worker
from accessforge_persistence import github_previews


def _config() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "appId": 7,
        "previewDigest": "a" * 64,
        **{
            key: str(uuid4())
            for key in (
                "workspaceId",
                "userId",
                "sessionId",
                "previewId",
                "approvalId",
            )
        },
    }


@pytest.mark.parametrize(
    "fault",
    [None, "readable", "parent", "symlink", "hardlink", "duplicate", "extra", "bool", "oversize"],
)
def test_private_closed_scope(tmp_path: Path, fault: str | None) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    config = _config()
    if fault == "extra":
        config["key"] = "not-allowed"
    if fault == "bool":
        config["appId"] = True
    raw = json.dumps(config)
    if fault == "duplicate":
        raw = raw[:-1] + ',"appId":7}'
    if fault == "oversize":
        raw += " " * 8192
    path = root / "scope.json"
    path.write_text(raw)
    path.chmod(0o644 if fault == "readable" else 0o600)
    if fault == "hardlink":
        (root / "alias.json").hardlink_to(path)
    if fault == "parent":
        root.chmod(0o755)
    if fault == "symlink":
        link = root / "link.json"
        link.symlink_to(path)
        path = link
    if fault:
        with pytest.raises((ValueError, OSError)):
            operator._scope(path)
    else:
        assert operator._scope(path) == config


def test_explicit_flag_required_before_any_file_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any) -> None:
        pytest.fail("file read before explicit flag")

    monkeypatch.setattr(operator, "_private_bytes", refuse)
    with pytest.raises(SystemExit) as error:
        operator.main(["--scope-file", "/absent/scope", "--key-file", "/absent/key"])
    assert error.value.code == 2


@pytest.mark.parametrize("fault", [None, "authority", "app", "publication"])
def test_operator_wires_exact_scope_and_never_prints_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str | None,
) -> None:
    scope = _config()
    calls: list[str] = []
    monkeypatch.setattr(operator, "_scope", lambda path: scope)
    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", "synthetic-db")

    class Connection:
        def execute(self, *args: Any) -> None:
            pass

    @contextmanager
    def connection(*args: Any) -> Any:
        yield Connection()

    def authorize(*args: Any) -> None:
        calls.append("authorize")
        if fault == "authority":
            raise ValueError("sensitive-provider-error")

    def key(path: Path, maximum: int) -> bytes:
        calls.append("key")
        assert maximum == 16384
        return b"synthetic-private-key"

    def sign(**kwargs: Any) -> AppJWT:
        calls.append("sign")
        assert kwargs == {"app_id": 7, "private_pem": b"synthetic-private-key"}
        return AppJWT(7, 123, "sensitive-bearer")

    store = object()

    def publish(database: str, **kwargs: Any) -> CreatedCheck:
        calls.append("publish")
        assert database == "synthetic-db"
        assert kwargs["principal"].session_id == scope["sessionId"]
        assert kwargs["principal"].workspace_id == scope["workspaceId"]
        assert kwargs["preview_id"] == scope["previewId"]
        assert kwargs["approval_id"] == scope["approvalId"]
        assert kwargs["expected_digest"] == scope["previewDigest"]
        assert kwargs["app_jwt"] == "sensitive-bearer"
        assert kwargs["store"] is store and kwargs["allow_temporary_token_issuance"] is True
        if fault == "publication":
            raise ValueError("sensitive-provider-error")
        return CreatedCheck(str(uuid4()), 101, scope["previewDigest"], "2026-09-16T00:00:00Z")

    monkeypatch.setattr(operator, "workspace_connection", connection)
    monkeypatch.setattr(operator, "_authorize", authorize)
    monkeypatch.setattr(
        github_previews,
        "read",
        lambda *args, **kwargs: {
            "preview_digest": scope["previewDigest"],
            "preview": {"identity": {"appId": "8" if fault == "app" else "7"}},
        },
    )
    monkeypatch.setattr(operator, "_private_bytes", key)
    monkeypatch.setattr(operator, "sign_app_jwt", sign)
    monkeypatch.setattr(purge_worker, "_store_from_environment", lambda: store)
    monkeypatch.setattr(operator, "publish_preview", publish)
    argv = [
        "--scope-file",
        "/private/scope",
        "--key-file",
        "/private/key",
        "--allow-github-publication",
    ]
    if fault:
        with pytest.raises(SystemExit) as error:
            operator.main(argv)
        assert error.value.code == (1 if fault == "publication" else 2)
    else:
        operator.main(argv)
    assert calls == (
        ["authorize"] if fault in ("authority", "app") else ["authorize", "key", "sign", "publish"]
    )
    output = capsys.readouterr()
    assert "sensitive" not in output.out + output.err
    assert "synthetic-private-key" not in output.out + output.err
    assert ("GITHUB_CREATION_RETAINED" in output.out) is (fault is None)
