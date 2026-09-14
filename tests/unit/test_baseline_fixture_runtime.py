"""Synthetic SQL rows test seed admission; not real fixture or reader evidence."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_orchestrator import baseline_fixture_runtime as seed
from accessforge_persistence import baseline_regressions
from accessforge_persistence.candidate_regressions import RegressionClaim


@pytest.mark.parametrize(
    "fault", [None, "nonce", "nonempty", "boolean", "stale", "before", "duplicate"]
)
def test_seed_confirmation_requires_original_empty_fresh_measurement(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    now = datetime.now(UTC)
    context: dict[str, Any] = dict(
        runId="run",
        workspaceId="ws",
        nonce="n" * 24,
        templateDigest="t" * 64,
        variant="inaccessible",
    )
    row = dict(
        context=context,
        context_digest=digest(context),
        created_at=now - timedelta(seconds=2),
        observation={} if fault == "duplicate" else None,
    )
    application: dict[str, Any] = {
        **{k: context[k] for k in ("nonce", "templateDigest", "variant")},
        "createdAt": to_rfc3339_utc(now - timedelta(seconds=1)),
        "observedAt": to_rfc3339_utc(now),
        "effectCount": 0,
    }
    if fault == "nonce":
        application["nonce"] = "other"
    elif fault == "nonempty":
        application["effectCount"] = 1
    elif fault == "boolean":
        application["effectCount"] = False
    elif fault in {"stale", "before"}:
        application["createdAt"] = to_rfc3339_utc(now - timedelta(seconds=20))
        if fault == "stale":
            row["created_at"] = now - timedelta(seconds=30)
            application["observedAt"] = application["createdAt"]

    class Connection:
        written: Any = None

        def transaction(self) -> Any:
            return nullcontext()

        def execute(self, query: str, args: Any = None) -> Any:
            if query.startswith("UPDATE"):
                self.written = args
            return self

        def fetchone(self) -> dict[str, Any]:
            return {**row, "now": now}

    conn = Connection()
    runtime = {"attemptId": "attempt", "buildId": "build", "workerEpoch": 1}
    monkeypatch.setattr(seed, "_live_context", lambda *a: runtime)
    monkeypatch.setattr(baseline_regressions, "assert_active", lambda *a, **kw: None)
    args: dict[str, Any] = dict(
        claim=RegressionClaim("attempt", "build", "token", 1),
        context=context,
        context_digest=digest(context),
        application=application,
    )
    if fault is None:
        receipt = seed.confirm(cast(psycopg.Connection[Any], conn), **args)
        assert receipt["baselineRuntime"] == runtime
        assert receipt["baselineRuntimeDigest"] == digest(runtime)
        assert conn.written is not None
    else:
        with pytest.raises(seed.Refused):
            seed.confirm(cast(psycopg.Connection[Any], conn), **args)
        assert conn.written is None
