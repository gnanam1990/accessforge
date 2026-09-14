"""Post-STOP ordering and private spool checks; synthetic services, no runtime verdict proof."""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from accessforge_orchestrator import complete_execution as completion
from accessforge_orchestrator import execution_artifacts as artifacts
from accessforge_persistence import evaluations


@pytest.mark.parametrize("stage", ["existing", "success", "read", "retain", "finalize"])
def test_completion_reconciles_before_reading_and_never_retries(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    events: list[str] = []
    original = {"meaning": "ORIGINAL_EVALUATION_SNAPSHOT", "snapshot": {"outcome": "INCONCLUSIVE"}}

    @contextmanager
    def connection(database: str, workspace: str) -> Any:
        assert (database, workspace) == ("database", "workspace")
        yield object()
        events.append("read-transaction-closed")

    def read(conn: Any, *, run_id: str) -> Any:
        assert run_id == "run"
        events.append("reconcile")
        if stage == "read":
            raise RuntimeError("synthetic read failure")
        return original if stage == "existing" else None

    def spool(path: str) -> bytes:
        assert path == "original-spool"
        events.append("spool")
        return b"original bytes\n"

    def retain(database: str, store: Any, **kwargs: Any) -> list[str]:
        assert kwargs == {
            "workspace_id": "workspace",
            "run_id": "run",
            "journal": b"original bytes\n",
        }
        events.append("retain")
        if stage == "retain":
            raise RuntimeError("synthetic lost retention acknowledgement")
        return ["artifact"]

    def finalize(database: str, store: Any, **kwargs: Any) -> Any:
        assert kwargs == {"workspace_id": "workspace", "run_id": "run"}
        events.append("finalize")
        if stage == "finalize":
            raise RuntimeError("synthetic lost evaluation acknowledgement")
        return original

    monkeypatch.setattr(completion, "workspace_connection", connection)
    monkeypatch.setattr(evaluations, "read", read)
    monkeypatch.setattr(completion, "read_private_journal", spool)
    monkeypatch.setattr(completion, "retain_bundle", retain)
    monkeypatch.setattr(completion, "finalize", finalize)
    kwargs: Any = dict(workspace_id="workspace", run_id="run", journal_path="original-spool")
    if stage in {"read", "retain", "finalize"}:
        with pytest.raises(RuntimeError, match="synthetic"):
            completion.complete("database", None, **kwargs)  # type: ignore[arg-type]
    else:
        assert completion.complete("database", None, **kwargs) is original  # type: ignore[arg-type]
    expected = ["reconcile"]
    if stage != "read":
        expected += ["read-transaction-closed"]
    if stage not in {"read", "existing"}:
        expected += ["spool", "retain"]
        if stage != "retain":
            expected += ["finalize"]
    assert events == expected


@pytest.mark.parametrize("case", ["private", "shared", "symlink", "pipe", "empty", "oversize"])
def test_private_spool_is_bounded_and_not_a_pipe_or_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 32)
    path = tmp_path / "journal"
    if case == "pipe":
        os.mkfifo(path, 0o600)
    else:
        path.write_bytes(b"" if case == "empty" else b"x" * (33 if case == "oversize" else 8))
        path.chmod(0o640 if case == "shared" else 0o600)
        if case == "symlink":
            link = tmp_path / "link"
            link.symlink_to(path)
            path = link
    if case == "private":
        assert artifacts.read_private_journal(str(path)) == b"x" * 8
    else:
        with pytest.raises((artifacts.Refused, OSError)):
            artifacts.read_private_journal(str(path))
