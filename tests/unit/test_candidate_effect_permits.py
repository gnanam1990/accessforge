"""Focused synthetic permission decisions for CI, not real reader/effect authorization proof."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import candidate_effects as permits
from accessforge_persistence import candidate_observations, supervisor_sessions


class Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class Store:
    def __init__(self, now: datetime, event: dict[str, Any]) -> None:
        self.now, self.event = now, event
        self.stored: dict[str, Any] | None = None
        self.binding = {
            "regression_attempt_id": "regression",
            "endpoint_binding_digest": "b" * 64,
            "expires_at": now + timedelta(seconds=30),
            "plan": {"path": "/form/fixture-only"},
        }

    def execute(self, query: str, args: Any = ()) -> Cursor:
        if query.startswith("SELECT b.*"):
            return Cursor(self.binding)
        if query.startswith("SELECT * FROM candidate_action_effect_permit"):
            return Cursor(self.stored)
        if query.startswith("SELECT e.*"):
            return Cursor(self.event)
        if query.startswith("SELECT clock_timestamp()"):
            return Cursor(
                {
                    "now": self.now,
                    **dict.fromkeys(
                        ("session_expiry", "deadline_at", "approval_expiry"),
                        self.now + timedelta(seconds=20),
                    ),
                }
            )
        assert query.startswith("INSERT INTO candidate_action_effect_permit")
        self.stored = dict(
            zip(
                (
                    "id",
                    "workspace_id",
                    "action_id",
                    "regression_attempt_id",
                    "preflight_event_id",
                    "grant_digest",
                    "grant_payload",
                    "granted_at",
                    "expires_at",
                ),
                args,
                strict=True,
            )
        )
        self.stored["grant_payload"] = args[6].obj
        return Cursor(self.stored)


@pytest.mark.parametrize(
    "fault", [None, "effect", "stop", "read", "unknown", "measurement", "stale"]
)
def test_exact_action_permission_is_bounded_and_never_rearmed(
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    now = datetime.now(UTC)
    manifest = {"permittedEffects": [] if fault == "effect" else ["FORM_SUBMIT"]}
    session = {
        "run_id": "run",
        "attempt_id": "attempt",
        "runner_id": "runner",
        "lease_id": "lease",
        "epoch": 1,
    }
    action = {
        "dispatched_at": now - timedelta(seconds=1),
        "action": "STOP" if fault == "stop" else "READ_CURRENT" if fault == "read" else "ACTIVATE",
        "action_sequence": 1,
    }
    source: dict[str, Any] = {
        "actionId": "action",
        "actionSequence": 1,
        "capturedAtUtc": to_rfc3339_utc(now - timedelta(seconds=20 if fault == "stale" else 0)),
        "checks": dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, "TRUE"),
    }
    if fault == "unknown":
        source["checks"]["BUILD_IDENTITY_MATCHES_MANIFEST"] = "UNKNOWN"
    measurement = {"fixture": "already-validated-server-receipt"}
    payload = {
        "serviceIdentity": "SUPERVISOR",
        "provenance": "RUNTIME_PROBE_REPORT",
        "sourceRecord": source,
        "sourceRecordDigest": digest(source),
        "buildArtifactReceipt": measurement,
    }
    event = {
        "event_id": "event",
        "event_type": "PREFLIGHT_RESULT",
        "payload": payload,
        "payload_digest": digest(payload),
        "manifest_digest": digest(manifest),
        "lease_epoch": 1,
    }
    store = Store(now, event)
    conn = cast(psycopg.Connection[dict[str, Any]], store)
    monkeypatch.setattr(supervisor_sessions, "_live", lambda *args: (session, manifest))
    monkeypatch.setattr(supervisor_sessions, "_action", lambda *args: action)
    monkeypatch.setattr(
        candidate_observations,
        "for_runtime_preflight",
        lambda *args, **kwargs: None if fault == "measurement" else measurement,
    )
    kwargs = {
        "workspace_id": "workspace",
        "session_id": "session",
        "action_id": "action",
        "token": "private",
    }
    if fault is not None:
        with pytest.raises(permits.Refused):
            permits.authorize_form(conn, **kwargs)
        assert store.stored is None
    else:
        result = permits.authorize_form(conn, **kwargs)
        assert result["expiresAt"] == to_rfc3339_utc(now + timedelta(seconds=5))
        assert store.stored is not None
        store.now += timedelta(seconds=3)
        assert permits.authorize_form(conn, **kwargs) == result
        store.stored["grant_payload"]["actionId"] = "other-action"
        with pytest.raises(permits.Refused, match="integrity"):
            permits.authorize_form(conn, **kwargs)
