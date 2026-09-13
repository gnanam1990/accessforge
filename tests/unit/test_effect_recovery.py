"""Historical projection cases, synthetic transport rows only (for CI)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import effect_recovery

RUN = str(UUID(int=100))


def row(phase: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    expiry = now + timedelta(seconds=5) if phase == "OPEN" else now - timedelta(seconds=1)
    grant = {
        "workspaceId": "workspace",
        "sessionId": "session",
        "actionId": "action",
        "runId": RUN,
        "attemptId": "attempt",
        "leaseId": "lease",
        "leaseEpoch": 1,
        "regressionAttemptId": "regression",
        "preflightEventId": "preflight",
        "expiresAt": to_rfc3339_utc(expiry),
        "path": "/form/private-fixture-nonce",
    }
    fingerprint = digest(grant)
    identifier = str(uuid5(NAMESPACE_URL, "accessforge:action-effect:" + fingerprint))
    consumed = phase not in {"OPEN", "CLOSED_UNUSED"}
    return {
        "id": identifier,
        "workspace_id": "workspace",
        "grant_payload": grant,
        "grant_digest": fingerprint,
        "action_id": "action",
        "regression_attempt_id": "regression",
        "preflight_event_id": "preflight",
        "requested_run_id": RUN,
        "action_attempt_id": "attempt",
        "action_lease_id": "lease",
        "action_epoch": 1,
        "expires_at": expiry,
        "snapshot_at": now,
        "granted_at": now - timedelta(seconds=5),
        "consumed_at": now if consumed else None,
        "delivery_id": identifier if consumed and phase != "DELIVERY_RECORD_MISSING" else None,
        "response_digest": "a" * 64 if phase == "RESPONSE_RETAINED" else None,
        "response_status": 422 if phase == "RESPONSE_RETAINED" else None,
        "responded_at": now if phase == "RESPONSE_RETAINED" else None,
        "action_sequence": 1,
        "action": "ACTIVATE",
        "result_status": None,
        "request_digest": "b" * 64 if consumed else None,
        "result_at": None,
        "released_at": None,
        "stop_acknowledged_at": None,
        "runner_quarantined_at": now,
        "endpoint_state": "CLOSED",
        "cleanup_confirmed": True,
        "run_status": "INTERRUPTED",
        "run_outcome": "INCONCLUSIVE",
        "run_quarantined": True,
    }


@pytest.mark.parametrize(
    "phase",
    ["OPEN", "CLOSED_UNUSED", "UNCONFIRMED", "DELIVERY_RECORD_MISSING", "RESPONSE_RETAINED"],
)
def test_historical_transport_does_not_supply_effect_success_or_reset_authority(phase: str) -> None:
    value = effect_recovery._item(row(phase))
    assert value["phase"] == phase
    assert value["requiresInvestigation"] is (phase in {"UNCONFIRMED", "DELIVERY_RECORD_MISSING"})
    assert value["actionResult"] is None and value["leaseReleasedAt"] is None
    assert "private-fixture-nonce" not in json.dumps(value)
    assert "grant_payload" not in value


def test_corrupt_or_foreign_original_permission_is_not_a_plausible_recovery_report() -> None:
    original = row("UNCONFIRMED")
    original["requested_run_id"] = "another-run"
    with pytest.raises(effect_recovery.Refused):
        effect_recovery._item(original)
    original = row("UNCONFIRMED")
    original["grant_payload"]["path"] = "changed-private-path"
    with pytest.raises(effect_recovery.Refused):
        effect_recovery._item(original)


def test_pagination_remains_bounded_and_does_not_hide_a_next_page() -> None:
    rows = sorted([row("OPEN"), row("UNCONFIRMED")], key=lambda item: item["id"])

    class Store:
        def execute(self, query: str, params: Any) -> Store:
            assert query.startswith("SELECT") and params == (None, None, 2, RUN)
            return self

        def fetchall(self) -> list[dict[str, Any]]:
            return rows

    result = effect_recovery.read(cast(psycopg.Connection[Any], Store()), run_id=RUN, limit=1)
    assert result is not None and len(result["items"]) == 1
    assert result["nextCursor"] == rows[0]["id"]
    assert result["providesRetryAuthority"] is False and result["providesResetAuthority"] is False
