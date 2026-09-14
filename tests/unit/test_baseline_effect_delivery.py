"""Synthetic delivery-state decisions for CI; actual authority remains in the live gate."""

from __future__ import annotations

from typing import Any, cast

import psycopg
import pytest

from accessforge_persistence import baseline_effect_delivery as delivery
from accessforge_persistence import baseline_effects
from accessforge_persistence.candidate_regressions import RegressionClaim


class Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class Store:
    def __init__(self) -> None:
        self.permit: dict[str, Any] = {
            "id": "permit",
            "workspace_id": "workspace",
            "consumed_at": None,
            "grant_payload": {"runId": "run", "path": "/form/fixture"},
        }
        self.response: dict[str, Any] | None = None
        self.claims = 0

    def execute(self, query: str, args: Any = ()) -> Rows:
        if query.startswith("SELECT run_id"):
            return Rows([{"run_id": "run"}])
        if query.startswith("SELECT p.id"):
            return Rows([] if self.permit["consumed_at"] else [{"id": "permit"}])
        if query.startswith("SELECT navigator_values"):
            return Rows(
                [
                    {
                        "navigator_values": {
                            "full_name": "Synthetic Example",
                            "email_invalid": "not-an-email",
                            "email_valid": "test.person@example.test",
                        }
                    }
                ]
            )
        if query.startswith("UPDATE baseline_action_effect_permit"):
            self.permit["consumed_at"] = "committed-once"
        elif query.startswith("INSERT INTO baseline_effect_delivery"):
            self.claims += 1
            self.response = {"response_digest": None, "request_digest": args[2]}
        elif query.startswith("SELECT * FROM baseline_effect_delivery"):
            return Rows([] if self.response is None else [self.response])
        elif query.startswith("UPDATE baseline_effect_delivery"):
            assert self.response is not None
            self.response["response_digest"] = args[0]
        else:
            raise AssertionError(query)
        return Rows([])


@pytest.mark.parametrize(
    ("body", "allowed"),
    [
        ("full_name=Synthetic+Example", True),
        ("", True),
        ("email=", True),
        ("full_name=Unapproved", False),
        ("email=x&email=y", False),
        ("full_name=not-an-email", False),
        ("email=Synthetic+Example", False),
        ("category=not-an-email", False),
        ("email=not-an-email", True),
        ("email=test.person%40example.test", True),
    ],
)
def test_consumption_never_replays_and_does_not_hide_unconfirmed_response(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    allowed: bool,
) -> None:
    store = Store()
    conn = cast(psycopg.Connection[dict[str, Any]], store)
    claim = RegressionClaim("regression", "build", "private", 1)
    permission = {"permitId": "permit", "expiresAt": "fixture-expiry"}
    # The authority gate has its own live validation; these cases isolate transport transitions.
    monkeypatch.setattr(delivery, "_live", lambda *args, **kwargs: store.permit)
    monkeypatch.setattr(baseline_effects, "_view", lambda row: dict(permission))
    if not allowed:
        with pytest.raises(delivery.Refused):
            delivery.begin(conn, claim=claim, path="/form/fixture", body=body)
        assert store.claims == 0
        return
    result = delivery.begin(conn, claim=claim, path="/form/fixture", body=body)
    assert result == permission and store.claims == 1
    assert delivery.check(conn, claim=claim, permission=permission)["response_digest"] is None
    with pytest.raises(delivery.Refused):
        delivery.begin(conn, claim=claim, path="/form/fixture", body=body)
    assert store.claims == 1
    delivery.retain_response(
        conn,
        claim=claim,
        permission=permission,
        response={"status": 422, "body": "invalid synthetic form"},
    )
    with pytest.raises(delivery.Refused):
        delivery.retain_response(
            conn,
            claim=claim,
            permission=permission,
            response={"status": 201, "body": "replacement"},
        )
