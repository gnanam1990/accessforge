"""Backup failures must not publish database tool output as safe diagnostics."""

import importlib.util
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "backup_diagnostics_target", Path(__file__).resolve().parents[2] / "scripts" / "backup.py"
)
assert _spec is not None and _spec.loader is not None
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)


@pytest.mark.parametrize("exit_code", [1, 2, -9])
def test_dump_failure_withholds_both_output_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exit_code: int,
) -> None:
    private = "PRIVATE_CONNECTION_OR_ROW_CANARY"
    monkeypatch.setattr(backup, "connect", lambda _: nullcontext(None))
    monkeypatch.setattr(backup, "assert_backup_run_integrity", lambda _: None)
    monkeypatch.setattr(backup.shutil, "which", lambda _: "/trusted/pg_dump")

    def failed(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=exit_code, stdout=private, stderr=private + "\nsecond line"
        )

    monkeypatch.setattr(backup.subprocess, "run", failed)
    with pytest.raises(SystemExit) as caught:
        backup._dump_postgres("postgresql://synthetic", tmp_path / "partial.dump")
    message = str(caught.value)
    assert private not in message
    assert f"exit {exit_code}" in message
    assert "incomplete" in message
    assert "must not be used" in message
