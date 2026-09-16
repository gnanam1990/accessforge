"""Atomic operator setup and audit against PostgreSQL, not OAuth acceptance."""

import uuid
from typing import Any

import psycopg
import pytest

from accessforge_api.auth import workspace_setup
from accessforge_persistence import (
    migrate,
    unscoped_connection,
    user_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def setup_args(test_database_url: str) -> dict[str, str]:
    migrate(test_database_url)
    user = str(uuid.uuid4())
    return {
        "user_id": user,
        "workspace_id": str(uuid.uuid4()),
        "email": user + "@example.test",
        "name": "Operator test workspace",
        "operator": "integration-test",
    }


def test_setup_creates_owner_and_both_audits_but_no_login(
    test_database_url: str, setup_args: dict[str, str]
) -> None:
    workspace_setup.provision_workspace(test_database_url, **setup_args)
    with user_connection(test_database_url, setup_args["user_id"]) as conn:
        rows = conn.execute("SELECT workspace_id,role FROM workspace_membership").fetchall()
        assert rows == [{"workspace_id": uuid.UUID(setup_args["workspace_id"]), "role": "OWNER"}]
    with unscoped_connection(test_database_url) as conn:
        assert conn.execute(
            "SELECT action FROM global_audit_event WHERE target_id=%s", (setup_args["user_id"],)
        ).fetchall() == [{"action": "operator.user_created"}]
        for query in (
            "SELECT 1 FROM github_user_identity WHERE user_id=%s",
            "SELECT 1 FROM user_session WHERE user_id=%s",
        ):
            assert conn.execute(query, (setup_args["user_id"],)).fetchall() == []
    with workspace_connection(test_database_url, setup_args["workspace_id"]) as conn:
        assert conn.execute("SELECT action FROM audit_event").fetchall() == [
            {"action": "operator.workspace_created"}
        ]


def test_conflicting_workspace_rolls_back_new_user(
    test_database_url: str, setup_args: dict[str, str]
) -> None:
    workspace_setup.provision_workspace(test_database_url, **setup_args)
    second = {**setup_args, "user_id": str(uuid.uuid4()), "email": "new-" + setup_args["email"]}
    with pytest.raises(workspace_setup.WorkspaceSetupRefused):
        workspace_setup.provision_workspace(test_database_url, **second)
    with unscoped_connection(test_database_url) as conn:
        assert (
            conn.execute("SELECT 1 FROM app_user WHERE id=%s", (second["user_id"],)).fetchone()
            is None
        )


def test_audit_failure_rolls_back_everything(
    test_database_url: str, setup_args: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise psycopg.OperationalError("synthetic audit failure")

    monkeypatch.setattr(workspace_setup, "record_audit_event", fail)
    with pytest.raises(psycopg.OperationalError):
        workspace_setup.provision_workspace(test_database_url, **setup_args)
    with unscoped_connection(test_database_url) as conn:
        assert (
            conn.execute("SELECT 1 FROM app_user WHERE id=%s", (setup_args["user_id"],)).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM workspace WHERE id=%s", (setup_args["workspace_id"],)
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM global_audit_event WHERE target_id=%s", (setup_args["user_id"],)
            ).fetchone()
            is None
        )
