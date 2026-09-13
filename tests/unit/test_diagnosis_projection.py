"""Projection assembly with inert persistence/source ports; not physical or S3 acceptance proof."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from accessforge_domain.canonical import digest
from accessforge_orchestrator.diagnosis import projection as delivery
from accessforge_orchestrator.diagnosis.models import SourceExcerpt
from accessforge_orchestrator.diagnosis.source import FrozenSourceScope
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_persistence import evaluations, journeys


@pytest.mark.parametrize("fault", [None, "pass", "artifacts", "source", "redacted", "unknown"])
def test_preparation_uses_original_evaluation_and_checked_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str | None
) -> None:
    workspace, run_id, attempt, evaluation = (str(uuid4()) for _ in range(4))
    manifest = {
        "sourceCommitSha": "a" * 40,
        "sourceTreeDigest": "b" * 64,
        "journeyVersionId": str(uuid4()),
        "assertionSetDigest": "c" * 64,
    }
    run = {
        "status": "COMPLETED",
        "outcome": "PASS" if fault == "pass" else "INCONCLUSIVE",
        "manifest_digest": digest(manifest),
        "lease_epoch": 1,
    }
    artifact_ids = [{"artifactId": str(uuid4()), "digest": "d" * 64}]
    snapshot = {
        "outcome": run["outcome"],
        "manifestDigest": run["manifest_digest"],
        "attemptId": attempt,
        "artifacts": artifact_ids,
        "assertions": [
            {"assertionId": "announcement", "condition": "UNKNOWN"},
            {"assertionId": "unrelated", "condition": "FALSE"},
        ],
    }
    original = {
        "evaluationId": evaluation,
        "snapshotDigest": digest(snapshot),
        "snapshot": snapshot,
    }
    queries = []

    class Connection:
        def execute(self, sql: str, params: object) -> Any:
            queries.append(sql)
            result: Any
            if "FROM run WHERE" in sql:
                assert params == (run_id, workspace)
                result = run
            elif "FROM sealed_manifest" in sql:
                result = {"canonical_manifest": manifest}
            else:
                assert "FROM supervisor_dispatch_ticket" in sql
                result = {"attempt_id": attempt, "epoch": 1, "accepted_at": "fixture"}
            return SimpleNamespace(fetchone=lambda: result)

    monkeypatch.setattr(evaluations, "read", lambda *args, **kwargs: original)
    monkeypatch.setattr(
        journeys,
        "load_assertion_contract",
        lambda *args, **kwargs: SimpleNamespace(
            required=(
                SimpleNamespace(assertion_id="announcement", description="Announce the error"),
                SimpleNamespace(assertion_id="unrelated", description="An unrelated failed step"),
            )
        ),
    )
    checks = []

    def retained(*args: Any) -> Any:
        checks.append("artifacts")
        if fault == "artifacts":
            raise Refused("retained bytes changed")
        return {
            "SPEECH_TRANSCRIPT": {
                "records": [
                    {
                        "eventId": str(uuid4()),
                        "eventType": "READER_OBSERVATION",
                        "payload": {
                            "serviceIdentity": "SUPERVISOR",
                            "sourceRecordDigest": "e" * 64,
                            "submittedSourceRecordDigest": "f" * 64
                            if fault == "redacted"
                            else "e" * 64,
                            "sourceRecord": {
                                "phrase": "recorded announcement",
                                "status": "CAPTURE_UNKNOWN" if fault == "unknown" else "SUCCEEDED",
                            },
                        },
                    }
                ]
            }
        }, artifact_ids

    def source(*args: Any) -> None:
        checks.append("source")
        if fault == "source":
            raise Refused("source tree changed")

    monkeypatch.setattr(delivery, "_retained", retained)
    monkeypatch.setattr(delivery, "_assert_source", source)
    monkeypatch.setattr(
        delivery,
        "FrozenSourceReader",
        lambda *args: SimpleNamespace(
            read=lambda *args, **kwargs: SourceExcerpt(
                path="form.ts",
                file_digest="f" * 64,
                line_start=1,
                line_end=1,
                text="fixture source",
            )
        ),
    )
    scope = FrozenSourceScope(tmp_path, "a" * 40, {"form.ts": "f" * 64})
    args: dict[str, Any] = {
        "workspace_id": workspace,
        "run_id": run_id,
        "assertion_id": "announcement",
        "component_name": "form",
        "source_scope": scope,
        "excerpts": (delivery.ExcerptRequest("form.ts", 1, 1),),
    }
    if fault in {"pass", "artifacts", "source"}:
        with pytest.raises(Refused):
            delivery.prepare(Connection(), object(), **args)  # type: ignore[arg-type]
        if fault == "pass":
            assert not checks
        return
    prepared = delivery.prepare(Connection(), object(), **args)  # type: ignore[arg-type]
    assert prepared.evaluation_digest == original["snapshotDigest"]
    assert prepared.projection.run_outcome == "INCONCLUSIVE"
    assert prepared.projection.assertions[0].condition == "UNKNOWN"
    assert len(prepared.projection.assertions) == 1
    assert checks == ["artifacts", "source", "source"]
    event = prepared.projection.evidence[1]
    assert event.observed_text == ("recorded announcement" if fault is None else None)
    assert (
        event.observation_state
        == {None: "RECORDED", "redacted": "REDACTED", "unknown": "CAPTURE_UNKNOWN"}[fault]
    )
