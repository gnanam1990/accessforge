"""Synthetic rows verify endpoint admission; these do not establish a live reader session."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import baseline_endpoints as endpoints
from accessforge_persistence import baseline_fixture_evidence, baseline_regressions
from accessforge_persistence.candidate_regressions import RegressionClaim


@pytest.mark.parametrize("fault", [None, "nonempty", "boolean", "fixture", "process", "meaning"])
def test_endpoint_requires_original_confirmed_empty_seed(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    context = dict(
        runId="run",
        workspaceId="ws",
        manifestDigest="manifest",
        fixtureId="fixture",
        nonce="nonce",
        templateDigest="template",
        variant="inaccessible",
    )
    application = dict(
        nonce="nonce", templateDigest="template", variant="inaccessible", effectCount=0
    )
    if fault == "nonempty":
        application["effectCount"] = 1
    elif fault == "boolean":
        application["effectCount"] = False
    observation: dict[str, Any] = dict(
        contextDigest=digest(context),
        application=application,
        baselineRuntime={},
        meaning="INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION",
    )
    if fault == "meaning":
        observation["meaning"] = "UNVERIFIED"
    row: dict[str, Any] = dict(
        context=context,
        context_digest=digest(context),
        observation=observation,
        observation_digest=digest(observation),
        manifest_digest="manifest",
        fixture_id="fixture",
        nonce="replaced" if fault == "fixture" else "nonce",
        template_digest="template",
        observed_at="now",
    )

    class Connection:
        def execute(self, *args: Any) -> Any:
            return self

        def fetchone(self) -> Any:
            return row

        def fetchall(self) -> Any:
            return [
                dict(role=r, state="REMOVED" if fault == "process" else "CREATED")
                for r in ("database", "driver", "candidate")
            ]

    monkeypatch.setattr(baseline_fixture_evidence, "validate_runtime", lambda *a, **kw: None)
    conn = cast(psycopg.Connection[Any], Connection())
    parent = dict(id="attempt", run_id="run", workspace_id="ws")
    if fault is None:
        assert endpoints._seed(conn, parent) == context
    else:
        with pytest.raises(endpoints.Refused):
            endpoints._seed(conn, parent)


@pytest.mark.parametrize("fault", [None, "origin", "digest", "expiry"])
def test_binding_cannot_change_committed_origin_or_bind_after_expiry(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    now = datetime.now(UTC)
    identity = {"listenOrigin": "http://127.0.0.1:8081", "taskId": "attempt"}
    receipt = {
        **identity,
        "origin": "http://127.0.0.1:8082" if fault == "origin" else identity["listenOrigin"],
    }
    receipt["bindingDigest"] = "wrong" if fault == "digest" else digest(receipt)
    row = dict(
        plan=identity,
        state="PLANNED",
        expires_at=now + timedelta(seconds=-1 if fault == "expiry" else 30),
    )

    class Connection:
        writes = 0

        def transaction(self) -> Any:
            return nullcontext()

        def execute(self, query: str, *args: Any) -> Any:
            if query.startswith("UPDATE"):
                self.writes += 1
            return self

        def fetchone(self) -> Any:
            return {"now": now}

    conn = Connection()
    monkeypatch.setattr(baseline_regressions, "_owned", lambda *a: {})
    monkeypatch.setattr(baseline_regressions, "assert_active", lambda *a, **kw: None)
    monkeypatch.setattr(endpoints, "_seed", lambda *a: {})
    monkeypatch.setattr(endpoints, "_record", lambda *a: row)
    args: dict[str, Any] = dict(
        claim=RegressionClaim("attempt", "build", "worker", 1), receipt=receipt
    )
    if fault is None:
        endpoints.bound(cast(psycopg.Connection[Any], conn), **args)
        assert conn.writes == 1
    else:
        with pytest.raises(endpoints.Refused):
            endpoints.bound(cast(psycopg.Connection[Any], conn), **args)
        assert conn.writes == 0
