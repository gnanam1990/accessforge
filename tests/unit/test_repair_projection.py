"""Synthetic persistence/evidence ports; not real-DB, broker or actual model acceptance."""

import hashlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from accessforge_build_worker.snapshot import SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import BoundCommitSource, PersistedCommitSource
from accessforge_domain.canonical import digest
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.repair import projection
from accessforge_persistence import evaluations


@pytest.mark.parametrize(
    "fault", [None, "permission", "baseline", "artifacts", "deleted", "source", "cancelled"]
)
def test_prepares_only_original_retained_authorized_complete_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str | None,
) -> None:
    ws, finding, diagnosis, actor, project, run, attempt, snapshot_id = (
        str(uuid4()) for _ in range(8)
    )
    source_file = SourceFile("src/form.ts", b"<button>Save</button>\r\n")
    source = SourceSnapshot((source_file, SourceFile("private-unrelated.txt", b"not model input")))
    manifest = {"sourceCommitSha": "a" * 40, "sourceTreeDigest": source.tree_digest}
    manifest_digest = digest(manifest)
    baseline = {
        "run_id": run,
        "assertion_id": "name",
        "manifest_digest": manifest_digest,
        "lease_epoch": 2,
        "outcome": "INCONCLUSIVE",
        "canonical_manifest": manifest,
        "project_id": project,
        "source_snapshot_id": snapshot_id,
        "commit_sha": "a" * 40,
        "tree_digest": source.tree_digest,
        "paths": ["src"],
        "surface_revision": 1,
    }
    evaluation = {
        "snapshotDigest": "e" * 64,
        "snapshot": {
            "outcome": "INCONCLUSIVE",
            "manifestDigest": manifest_digest,
            "attemptId": attempt,
            "artifacts": [{"artifactId": "artifact-1"}],
            "assertions": [{"assertionId": "name", "condition": "FALSE"}],
        },
    }
    analysis = {
        "support": "SOURCE_LINKED",
        "missing_information": [],
        "hypothesis": {
            "observed_obstacle": "Unknown button name",
            "affected_task_step": "Save",
            "source_location": {
                "path": source_file.path,
                "file_digest": hashlib.sha256(source_file.content).hexdigest(),
                "line_start": 1,
                "line_end": 1,
            },
            "supporting_evidence_ids": ["artifact-1"],
            "alternative_explanations": ["Focus moved"],
            "uncertainty": "Independent rerun required",
        },
        "repair_brief": {
            "allowed_files": [source_file.path],
            "intended_behavior": "Name the button",
            "functional_constraints": ["Preserve submission and validation"],
            "protected_surfaces": ["tests"],
            "stop_recommendation": None,
        },
    }
    record = {
        "payload": analysis,
        "payload_digest": digest(analysis),
        "evaluation_digest": "e" * 64,
        "attempt_id": attempt,
    }
    ticket = {
        "run_id": run,
        "workspace_id": ws,
        "attempt_id": attempt,
        "epoch": 2,
        "accepted_at": "fixture",
    }
    calls: list[str] = []
    fence = Event()

    class Connection:
        def execute(self, sql: str, params: Any) -> Any:
            result: Any
            if "workspace_membership" in sql:
                assert params == (ws, actor)
                result = {"role": "VIEWER" if fault == "permission" else "MAINTAINER"}
            elif "FROM finding f" in sql:
                assert params == (finding, ws)
                assert "p.repository_authorized_by IS NOT NULL" in sql and "FOR SHARE" in sql
                result = None if fault == "baseline" else baseline
            elif "supervisor_dispatch_ticket" in sql:
                result = ticket
            else:
                assert "finding_diagnosis d" in sql and "successor.supersedes=d.id" in sql
                assert calls == ["artifacts"]  # Retention lock precedes diagnosis lock.
                result = None if fault == "deleted" else record
            return SimpleNamespace(fetchone=lambda: result)

    def artifacts(*args: Any) -> Any:
        calls.append("artifacts")
        if fault == "artifacts":
            raise Refused("fixture evidence unavailable")
        return {}, evaluation["snapshot"]["artifacts"]  # type: ignore[index]

    def broker(*args: Any, **kwargs: Any) -> PersistedCommitSource:
        calls.append("source")
        assert (
            kwargs["repositories"] == {project: tmp_path}
            and kwargs["source_snapshot_id"] == snapshot_id
        )
        if fault == "cancelled":
            fence.set()
        return PersistedCommitSource(
            ws,
            project,
            snapshot_id,
            BoundCommitSource("b" * 40 if fault == "source" else "a" * 40, source),
        )

    monkeypatch.setattr(evaluations, "read", lambda *args, **kwargs: evaluation)
    monkeypatch.setattr(projection, "_retained", artifacts)
    monkeypatch.setattr(projection, "read_persisted_source", broker)
    kwargs: dict[str, Any] = {
        "workspace_id": ws,
        "finding_id": finding,
        "diagnosis_id": diagnosis,
        "requested_by": actor,
        "repositories": {project: tmp_path},
        "cancelled": fence.is_set,
    }
    if fault is not None:
        with pytest.raises(Refused):
            projection.prepare(Connection(), object(), **kwargs)  # type: ignore[arg-type]
        if fault in {"permission", "baseline"}:
            assert calls == []
        return
    prepared = projection.prepare(Connection(), object(), **kwargs)  # type: ignore[arg-type]
    assert calls == ["artifacts", "source"]
    assert prepared.inputs.files[0].text == "<button>Save</button>\r\n"
    assert len(prepared.inputs.files) == 1
    assert "not model input" not in prepared.inputs.model_dump_json()
    assert prepared.source_archive_digest == source.archive_digest
    assert len(prepared.binding_digest) == 64 and prepared.requested_by == actor
