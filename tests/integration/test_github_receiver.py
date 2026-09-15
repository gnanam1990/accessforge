"""In-process ingress plus real PostgreSQL; no actual GitHub delivery or deployment."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from accessforge_orchestrator.github_receiver import ReceiverConfig, create_receiver
from accessforge_persistence import (
    github_bindings,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration
WS = str(UUID(int=133))
SECRET = b"synthetic-webhook-receiver-test-secret"
BODY = b'{"installation":{"id":42},"repository":{"id":13},"action":"not-authority"}'


@pytest.fixture
def config(test_database_url: str) -> ReceiverConfig:
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace CASCADE")
        conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'receiver-test')", (WS,))
    with workspace_connection(test_database_url, WS) as conn:
        binding = github_bindings.record_verified(
            conn,
            workspace_id=WS,
            app_id=7,
            installation_id=42,
            account_id=3,
            repository_id=13,
            owner="fixture-owner",
            name="fixture-repository",
            observed_at=datetime.now(UTC).isoformat(),
        )
    return ReceiverConfig(WS, binding, 7, test_database_url, SECRET)


def headers(body: bytes = BODY, delivery: int = 1) -> dict[str, str]:
    return {
        "x-hub-signature-256": "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest(),
        "x-github-delivery": str(UUID(int=delivery)),
        "content-type": "application/json",
    }


def test_receipt_replay_and_header_aliases_commit_without_dispatch(config: ReceiverConfig) -> None:
    with TestClient(create_receiver(config)) as client:
        for delivery in (1, 1, 2):
            response = client.post(
                "/webhook",
                content=BODY,
                headers={
                    **headers(delivery=delivery),
                    "x-workspace-id": str(UUID(int=999)),
                    "x-github-event": "please-publish",
                },
            )
            assert response.status_code == 202
            assert response.json() == {"status": "AUTHENTICATED_RECEIPT_ONLY"}
        assert client.get("/openapi.json").status_code == 404
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_delivery").fetchone() == {
            "n": 2
        }
        assert conn.execute("SELECT id FROM run").fetchall() == []


def test_single_url_receives_then_revokes_without_header_authority(config: ReceiverConfig) -> None:
    removed = json.dumps(
        {
            "action": "removed",
            "installation": {"id": 42, "app_id": 7},
            "repositories_removed": [{"id": 13}],
        }
    ).encode()
    with TestClient(create_receiver(config)) as client:
        assert client.post("/events", content=BODY, headers=headers()).status_code == 202
        response = client.post(
            "/events", content=removed, headers={**headers(removed, 2), "x-github-event": "push"}
        )
        assert response.status_code == 202
        assert response.json() == {"status": "ACCEPTED_LOCAL_EVENT"}
        assert client.post("/events", content=BODY, headers=headers(delivery=3)).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 2}
        assert conn.execute("SELECT id FROM run").fetchall() == []


@pytest.mark.parametrize(
    "body",
    [
        b'{"installation":{"id":43},"repository":{"id":13}}',
        b'{"installation":{"id":42},"repository":{"id":14}}',
        b'{"installation":{"id":42}}',
    ],
)
def test_signed_wrong_scope_cannot_enter_inbox(config: ReceiverConfig, body: bytes) -> None:
    with TestClient(create_receiver(config)) as client:
        assert client.post("/webhook", content=body, headers=headers(body)).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []


def test_disconnect_refuses_previously_accepted_bytes(config: ReceiverConfig) -> None:
    with TestClient(create_receiver(config)) as client:
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 202
        with workspace_connection(config.database_url, WS) as conn:
            github_bindings.disconnect(conn, workspace_id=WS, binding_id=config.binding_id)
        assert client.post("/webhook", content=BODY, headers=headers(delivery=2)).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_delivery").fetchone() == {
            "n": 1
        }


def test_explicit_repository_removal_revokes_and_replays_atomically(config: ReceiverConfig) -> None:
    body = json.dumps(
        {
            "installation": {"id": 42, "app_id": 7},
            "action": "removed",
            "repositories_removed": [{"id": 13}],
        }
    ).encode()
    with TestClient(create_receiver(config)) as client:
        for delivery in (10, 10, 11):
            response = client.post(
                "/repository-removal",
                content=body,
                headers={**headers(body, delivery), "x-github-event": "push"},
            )
            assert response.status_code == 202
            assert response.json() == {"status": "LOCAL_BINDING_REVOKED"}
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        row = conn.execute(
            "SELECT revoked_at FROM github_repository_binding WHERE id=%s", (config.binding_id,)
        ).fetchone()
        assert row is not None and row["revoked_at"] is not None
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_delivery").fetchone() == {
            "n": 2
        }


@pytest.mark.parametrize(
    "action,app_id,installation_id,removed",
    [
        ("added", 7, 42, [{"id": 13}]),
        ("removed", 8, 42, [{"id": 13}]),
        ("removed", 7, 43, [{"id": 13}]),
        ("removed", 7, 42, [{"id": 14}]),
        ("removed", 7, 42, []),
        ("removed", 7, 42, [{"id": True}]),
    ],
)
def test_wrong_removal_does_not_revoke(
    config: ReceiverConfig,
    action: str,
    app_id: int,
    installation_id: int,
    removed: list[dict[str, int]],
) -> None:
    body = json.dumps(
        {
            "action": action,
            "installation": {"id": installation_id, "app_id": app_id},
            "repositories_removed": removed,
        }
    ).encode()
    with TestClient(create_receiver(config)) as client:
        assert (
            client.post("/repository-removal", content=body, headers=headers(body)).status_code
            == 403
        )
    with workspace_connection(config.database_url, WS) as conn:
        assert github_bindings.require_live(conn, workspace_id=WS, binding_id=config.binding_id)
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []


def test_reused_header_with_different_authenticated_bytes_refuses(config: ReceiverConfig) -> None:
    changed = BODY + b" "
    with TestClient(create_receiver(config)) as client:
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 202
        assert client.post("/webhook", content=changed, headers=headers(changed)).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}


def test_conflicting_removal_delivery_rolls_back_without_revocation(config: ReceiverConfig) -> None:
    body = json.dumps(
        {
            "action": "removed",
            "installation": {"id": 42, "app_id": 7},
            "repositories_removed": [{"id": 13}],
        }
    ).encode()
    with TestClient(create_receiver(config)) as client:
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 202
        assert (
            client.post("/repository-removal", content=body, headers=headers(body)).status_code
            == 403
        )
    with workspace_connection(config.database_url, WS) as conn:
        assert github_bindings.require_live(conn, workspace_id=WS, binding_id=config.binding_id)
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}


def test_installation_suspension_revokes_without_automatic_unsuspend(
    config: ReceiverConfig,
) -> None:
    payload = {
        "action": "suspend",
        "installation": {"id": 42, "app_id": 7, "suspended_at": "2026-09-15T00:00:00Z"},
    }
    body = json.dumps(payload).encode()
    with TestClient(create_receiver(config)) as client:
        for delivery in (20, 20, 21):
            response = client.post(
                "/installation-suspension", content=body, headers=headers(body, delivery)
            )
            assert response.status_code == 202
            assert response.json() == {"status": "LOCAL_BINDING_REVOKED"}
        payload["action"] = "unsuspend"
        resumed = json.dumps(payload).encode()
        assert (
            client.post(
                "/installation-suspension", content=resumed, headers=headers(resumed, 22)
            ).status_code
            == 403
        )
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}


@pytest.mark.parametrize(
    "change",
    [
        {"id": 43},
        {"app_id": 8},
        {"app_id": True},
        {"suspended_at": None},
        {"suspended_at": "not-a-time"},
    ],
)
def test_wrong_suspension_keeps_binding_live(
    config: ReceiverConfig, change: dict[str, Any]
) -> None:
    body = json.dumps(
        {
            "action": "suspend",
            "installation": {
                "id": 42,
                "app_id": 7,
                "suspended_at": "2026-09-15T00:00:00Z",
                **change,
            },
        }
    ).encode()
    with TestClient(create_receiver(config)) as client:
        assert (
            client.post("/installation-suspension", content=body, headers=headers(body)).status_code
            == 403
        )
    with workspace_connection(config.database_url, WS) as conn:
        assert github_bindings.require_live(conn, workspace_id=WS, binding_id=config.binding_id)
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []


def test_ambiguous_headers_compression_and_oversize_refuse(config: ReceiverConfig) -> None:
    with TestClient(create_receiver(config)) as client:
        duplicate = list(headers().items()) + [("x-github-delivery", str(UUID(int=2)))]
        assert client.post("/webhook", content=BODY, headers=duplicate).status_code == 400
        assert (
            client.post(
                "/webhook",
                content=BODY,
                headers={
                    **headers(),
                    "content-encoding": "gzip",
                },
            ).status_code
            == 415
        )
        assert (
            client.post("/webhook", content=b"x" * 1_048_577, headers=headers()).status_code == 413
        )
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT id FROM github_webhook_body").fetchall() == []


def test_bad_signature_never_opens_database(
    config: ReceiverConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unauthenticated delivery reached database")

    monkeypatch.setattr("accessforge_orchestrator.github_receiver.workspace_connection", forbidden)
    with TestClient(create_receiver(config)) as client:
        response = client.post(
            "/webhook",
            content=BODY,
            headers={
                **headers(),
                "x-hub-signature-256": "sha256=" + "0" * 64,
            },
        )
        assert response.status_code == 403 and response.json() == {"status": "REFUSED"}
    assert SECRET.decode() not in repr(config) and config.database_url not in repr(config)
