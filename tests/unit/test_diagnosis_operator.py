"""Private file and explicit dispatch boundaries; no database/provider/reader calls."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from accessforge_orchestrator.diagnosis.operator import main
from accessforge_orchestrator.diagnosis.provisioning import ProvisioningRefused, load_source_scope


@pytest.mark.parametrize(
    "fault", [None, "workspace", "readable", "parent", "link", "duplicate", "scope"]
)
def test_only_private_exact_request_provisioning_is_accepted(
    tmp_path: Path, fault: str | None
) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    source = root / "source"
    source.mkdir()
    workspace, request = str(uuid4()), str(uuid4())
    path = root / "scope.json"
    config = {
        "schemaVersion": 1,
        "workspaceId": workspace,
        "requestId": request,
        "sourceRoot": str(source),
        "sourceCommitSha": "a" * 40,
        "allowedFileDigests": {"src/form.ts": "b" * 64},
    }
    if fault == "workspace":
        config["workspaceId"] = str(uuid4())
    if fault == "scope":
        config["allowedFileDigests"] = {"../secret": "b" * 64}
    raw = json.dumps(config)
    if fault == "duplicate":
        raw = raw[:-1] + ', "schemaVersion": 1}'
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o644 if fault == "readable" else 0o600)
    if fault == "parent":
        root.chmod(0o755)
    if fault == "link":
        link = root / "linked.json"
        link.symlink_to(path)
        path = link
    if fault is not None:
        with pytest.raises((ProvisioningRefused, OSError)):
            load_source_scope(path, workspace_id=workspace, request_id=request)
        return
    scope = load_source_scope(path, workspace_id=workspace, request_id=request)
    assert scope.root == source and scope.allowed_file_digests == {"src/form.ts": "b" * 64}
    assert scope.commit_sha == "a" * 40


def test_operator_requires_explicit_billable_flag_before_reading_any_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as refused:
        main(
            [
                "--workspace-id",
                str(uuid4()),
                "--request-id",
                str(uuid4()),
                "--scope-file",
                "/nonexistent/no-scope-file.json",
            ]
        )
    assert refused.value.code == 2
    assert "no provider work was started" in capsys.readouterr().err
