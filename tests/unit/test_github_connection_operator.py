"""Connection command composition only; no actual App identity, key or HTTP."""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from accessforge_orchestrator import github_connection_operator as operator
from accessforge_orchestrator.github_app_jwt import AppJWT


def _config() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "workspaceId": str(uuid4()),
        "userId": str(uuid4()),
        "sessionId": str(uuid4()),
        "appId": 7,
        "installationId": 42,
        "accountId": 3,
        "repositoryId": 13,
        "owner": "fixture-owner",
        "repository": "fixture-repository",
    }


@pytest.mark.parametrize("fault", [None, "extra", "id", "path", "duplicate"])
def test_connection_scope_is_closed(tmp_path: Path, fault: str | None) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    config = _config()
    if fault == "extra":
        config["previewId"] = str(uuid4())
    if fault == "id":
        config["installationId"] = True
    if fault == "path":
        config["owner"] = "../escape"
    raw = json.dumps(config)
    if fault == "duplicate":
        raw = raw[:-1] + ',"appId":7}'
    path = root / "connection.json"
    path.write_text(raw)
    path.chmod(0o600)
    if fault:
        with pytest.raises(ValueError):
            operator._scope(path)
    else:
        principal, scope = operator._scope(path)
        assert principal.session_id == config["sessionId"]
        assert scope.repository_id == 13 and scope.installation_id == 42


def test_no_flag_means_no_private_file_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any) -> None:
        pytest.fail("unexpected file read")

    monkeypatch.setattr(operator, "_private_bytes", fail)
    with pytest.raises(SystemExit) as error:
        operator.main(["--scope-file", "/absent/scope", "--key-file", "/absent/key"])
    assert error.value.code == 2


@pytest.mark.parametrize("fault", [None, "authority", "connection"])
def test_connection_checks_authority_before_loading_key_and_redacts_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str | None,
) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    scope_path, key_path = root / "scope.json", root / "key.pem"
    config = _config()
    scope_path.write_text(json.dumps(config))
    scope_path.chmod(0o600)
    key_path.write_bytes(b"synthetic-key")
    key_path.chmod(0o600)
    calls: list[str] = []

    class Connection:
        def execute(self, *args: Any) -> None:
            pass

    @contextmanager
    def connection(*args: Any) -> Any:
        yield Connection()

    def authorize(*args: Any) -> None:
        calls.append("authorize")
        if fault == "authority":
            raise ValueError("sensitive-error")

    def sign(**kwargs: Any) -> AppJWT:
        calls.append("sign")
        assert kwargs == {"app_id": 7, "private_pem": b"synthetic-key"}
        return AppJWT(7, 123, "sensitive-bearer")

    def connect(database: str, **kwargs: Any) -> str:
        calls.append("connect")
        assert database == "synthetic-db"
        assert kwargs["scope"].installation_id == 42
        assert kwargs["scope"].repository_id == 13
        assert kwargs["principal"].session_id == config["sessionId"]
        assert kwargs["app_jwt"] == "sensitive-bearer"
        assert kwargs["allow_temporary_token_issuance"] is True
        if fault == "connection":
            raise ValueError("sensitive-error")
        return str(uuid4())

    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", "synthetic-db")
    monkeypatch.setattr(operator, "workspace_connection", connection)
    monkeypatch.setattr(operator, "_authorize", authorize)
    monkeypatch.setattr(operator, "sign_app_jwt", sign)
    monkeypatch.setattr(operator, "connect_repository", connect)
    argv = [
        "--scope-file",
        str(scope_path),
        "--key-file",
        str(key_path),
        "--allow-installation-token-issuance",
    ]
    if fault:
        with pytest.raises(SystemExit) as error:
            operator.main(argv)
        assert error.value.code == (2 if fault == "authority" else 1)
    else:
        operator.main(argv)
    assert calls == (["authorize"] if fault == "authority" else ["authorize", "sign", "connect"])
    output = capsys.readouterr()
    assert "sensitive" not in output.out + output.err
    assert ("publication is not approved" in output.out) is (fault is None)
