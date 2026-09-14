"""Synthetic DB/authority boundaries; committed object recovery is covered by test_source_broker."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest

from accessforge_build_worker import baseline_source as baseline
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import BoundCommitSource, PersistedCommitSource
from accessforge_domain.canonical import digest
from accessforge_persistence import execution_approvals


@pytest.mark.parametrize("fault", [None, "revoked", "binding", "tree", "project", "cancelled"])
def test_source_read_releases_authority_locks_then_rechecks_before_return(
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
) -> None:
    source = SourceSnapshot((SourceFile("src/app.py", b"not executed"),))
    original = baseline.BaselineBinding(
        "workspace", "run", "project", "m" * 64, "snapshot", source.tree_digest, "a" * 64
    )
    events: list[str] = []
    reads = 0

    @contextmanager
    def connection(*args: Any) -> Iterator[object]:
        events.append("open")
        yield object()
        events.append("close")

    def binding(*args: Any, **kwargs: Any) -> baseline.BaselineBinding:
        nonlocal reads
        reads += 1
        events.append("authority")
        if reads == 2 and fault == "revoked":
            raise SnapshotRefused("revoked")
        return (
            replace(original, manifest_digest="changed")
            if reads == 2 and fault == "binding"
            else original
        )

    def recover(*args: Any, **kwargs: Any) -> PersistedCommitSource:
        assert events == ["open", "authority", "close", "open"]
        events.append("source")
        assert kwargs["source_snapshot_id"] == "snapshot"
        recovered = (
            SourceSnapshot((SourceFile("src/other", b"changed"),)) if fault == "tree" else source
        )
        return PersistedCommitSource(
            "workspace",
            "other" if fault == "project" else "project",
            "snapshot",
            BoundCommitSource("a" * 40, recovered),
        )

    monkeypatch.setattr(baseline, "workspace_connection", connection)
    monkeypatch.setattr(baseline, "_binding", binding)
    monkeypatch.setattr(baseline, "read_persisted_source", recover)
    args: dict[str, Any] = {
        "workspace_id": "workspace",
        "run_id": "run",
        "repositories": {},
        "cancelled": lambda: fault == "cancelled" and "source" in events,
    }
    if fault is None:
        result = baseline.prepare_baseline_source("unused", **args)
        assert result.binding == original and result.source == source
        assert events == [
            "open",
            "authority",
            "close",
            "open",
            "source",
            "close",
            "open",
            "authority",
            "close",
        ]
    else:
        with pytest.raises(SnapshotRefused):
            baseline.prepare_baseline_source("unused", **args)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "workspace",
        "leased",
        "candidate",
        "cancelled",
        "quarantined",
        "running",
        "seal",
        "digest",
        "authorization",
        "project",
    ],
)
def test_only_original_unleased_approved_baseline_is_preparable(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    manifest = {
        "authorizationId": "authorization",
        "sourceTreeDigest": "s" * 64,
        "buildArtifactDigest": "b" * 64,
    }
    run = {
        "workspace_id": "workspace",
        "project_id": "project",
        "status": "QUEUED",
        "cancel_requested_at": None,
        "quarantined": False,
        "authorization_id": "authorization",
        "manifest_digest": digest(manifest),
    }
    seal = {"id": "seal", "project_id": "project", "source_snapshot_id": "snapshot"}
    if fault == "workspace":
        run["workspace_id"] = "other"
    elif fault == "cancelled":
        run["cancel_requested_at"] = "now"
    elif fault == "quarantined":
        run["quarantined"] = True
    elif fault == "running":
        run["status"] = "RUNNING"
    elif fault == "digest":
        run["manifest_digest"] = "d" * 64
    elif fault == "authorization":
        run["authorization_id"] = "other"
    elif fault == "project":
        seal["project_id"] = "other"

    class Connection:
        value: Any = None

        def execute(self, query: str, args: Any) -> "Connection":
            if "FROM run WHERE" in query:
                self.value = run
            elif "FROM desktop_lease" in query:
                self.value = {"id": "lease"} if fault == "leased" else None
            elif "FROM candidate_run_binding" in query:
                self.value = {"id": "candidate"} if fault == "candidate" else None
            elif "FROM sealed_manifest" in query:
                self.value = None if fault == "seal" else seal
            else:
                self.value = {"id": "authorization"}
            return self

        def fetchone(self) -> Any:
            return self.value

    monkeypatch.setattr(
        execution_approvals, "assert_authorized", lambda *a, **kw: manifest
    )
    conn: Any = Connection()
    if fault is None:
        assert (
            baseline._binding(conn, workspace_id="workspace", run_id="run").expected_artifact_digest
            == "b" * 64
        )
    else:
        with pytest.raises(SnapshotRefused):
            baseline._binding(conn, workspace_id="workspace", run_id="run")
