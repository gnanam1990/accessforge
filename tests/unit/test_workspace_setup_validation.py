"""Provisioning argument validation must happen before database access."""

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest

from accessforge_api.auth.workspace_setup import WorkspaceSetupRefused, provision_workspace


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", "not-a-uuid"),
        ("workspace_id", "not-a-uuid"),
        ("email", "missing-at"),
        ("email", "a@b\n"),
        ("email", "a" * 255 + "@b"),
        ("name", ""),
        ("name", " extra whitespace "),
        ("name", "name\nline"),
        ("operator", "untrusted label\n"),
    ],
)
def test_invalid_setup_never_opens_database(field: str, value: str) -> None:
    args = {
        "user_id": str(uuid.uuid4()),
        "workspace_id": str(uuid.uuid4()),
        "email": "owner@example.test",
        "name": "Workspace",
        "operator": "test-operator",
    }
    args[field] = value
    with pytest.raises(WorkspaceSetupRefused, match="invalid"):
        provision_workspace("not-a-database", **args)


def test_cli_requires_confirmation_and_redacts_database_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location(
        "workspace_setup_cli",
        Path(__file__).resolve().parents[2] / "scripts/provision_workspace.py",
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    args = [
        "--user-id",
        str(uuid.uuid4()),
        "--workspace-id",
        str(uuid.uuid4()),
        "--email",
        "owner@example.test",
        "--name",
        "Workspace",
        "--operator",
        "test",
    ]
    calls = []

    def fail(*args: Any, **kwargs: Any) -> None:
        calls.append(True)
        raise psycopg.OperationalError("SECRET_DSN_CANARY")

    monkeypatch.setattr(cli, "provision_workspace", fail)
    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", "SECRET_DSN_CANARY")
    assert cli.main(args) == 2
    assert calls == []
    assert cli.main([*args, "--confirm-create"]) == 1
    assert calls == [True]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SECRET_DSN_CANARY" not in captured.err
    assert "unconfirmed" in captured.err
