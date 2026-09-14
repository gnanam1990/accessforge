"""In-process ingress plus real PostgreSQL; no actual GitHub delivery or deployment."""

import hashlib
import hmac
from datetime import UTC, datetime
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


def test_reused_header_with_different_authenticated_bytes_refuses(config: ReceiverConfig) -> None:
    changed = BODY + b" "
    with TestClient(create_receiver(config)) as client:
        assert client.post("/webhook", content=BODY, headers=headers()).status_code == 202
        assert client.post("/webhook", content=changed, headers=headers(changed)).status_code == 403
    with workspace_connection(config.database_url, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM github_webhook_body").fetchone() == {"n": 1}


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
