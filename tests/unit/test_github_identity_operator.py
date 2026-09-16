"""Operator entrypoint argument safety and redaction; no database connection."""

import importlib.util
from pathlib import Path
from typing import Any

import psycopg
import pytest

from accessforge_api.auth.github_accounts import bind_account
from accessforge_api.auth.github_identity import GitHubIdentityError

_spec = importlib.util.spec_from_file_location(
    "github_identity_operator_target",
    Path(__file__).resolve().parents[2] / "scripts" / "github_user_identity.py",
)
assert _spec is not None and _spec.loader is not None
operator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(operator)

ARGS = ["--github-user-id", "123", "--operator", "test-operator"]


@pytest.mark.parametrize("subject", ["0", "01", "-1", "x", str(2**63), "9" * 100])
def test_invalid_subject_refuses_before_opening_database(subject: str) -> None:
    with pytest.raises(GitHubIdentityError):
        bind_account(
            "not-a-database",
            github_subject=subject,
            user_id="00000000-0000-0000-0000-000000000001",
            operator="test",
        )


def test_missing_database_is_not_an_implicit_local_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCESSFORGE_DATABASE_URL", raising=False)
    assert operator.main(["revoke", *ARGS]) == 2


@pytest.mark.parametrize("args", [["bind", *ARGS], ["revoke", *ARGS, "--user-id", "unwanted"]])
def test_inconsistent_command_arguments_do_not_reach_database(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", "not-a-database")
    assert operator.main(args) == 2


def test_database_failure_does_not_print_credentials_or_claim_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private = "PRIVATE_DSN_AND_IDENTITY_CANARY"
    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", private)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise psycopg.OperationalError(private)

    monkeypatch.setattr(operator, "revoke_binding", fail)
    assert operator.main(["revoke", *ARGS]) == 1
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err
    assert captured.out == ""
    assert "unconfirmed" in captured.err


def test_explicit_bind_dispatches_only_the_named_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESSFORGE_DATABASE_URL", "test-db")
    calls: list[tuple[str, dict[str, str]]] = []

    def bind(db: str, **kwargs: str) -> None:
        calls.append((db, kwargs))

    monkeypatch.setattr(operator, "bind_account", bind)
    assert operator.main(["bind", *ARGS, "--user-id", "local-user-uuid"]) == 0
    assert calls == [
        (
            "test-db",
            {"github_subject": "123", "user_id": "local-user-uuid", "operator": "test-operator"},
        )
    ]
