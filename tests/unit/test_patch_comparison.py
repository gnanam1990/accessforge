"""Comparison fixtures only. No provider, source script or actual candidate is executed."""

from dataclasses import replace
from typing import Any

import pytest

from accessforge_build_worker.compare_command import main
from accessforge_build_worker.comparison import compare_source
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import BoundCommitSource
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import PatchStatus
from accessforge_persistence.patches import PatchProposal, patch_digest


def _proposal(source: SourceSnapshot, *changes: ProposedChange) -> PatchProposal:
    return PatchProposal(
        patch_id="patch-1",
        finding_id="finding-1",
        base_manifest_digest="a" * 64,
        base_source_digest=source.tree_digest,
        patch_digest=patch_digest(changes),
        changes=changes,
        verdicts=tuple((c.path, "ALLOWED") for c in changes),
        status=PatchStatus.PROPOSED,
        approval_id=None,
        proposed_by="author",
        rationale="Label the form",
        revision=1,
        created_at="2026-09-13T00:00:00Z",
    )


def _compare(source: SourceSnapshot, *changes: ProposedChange) -> dict[str, Any]:
    return compare_source(
        BoundCommitSource("b" * 40, source),
        patch=_proposal(source, *changes),
        application_paths=("src",),
    )


def test_complete_original_modified_added_deleted_and_mode_only_comparison() -> None:
    source = SourceSnapshot(
        (
            SourceFile("src/form", b"before\n"),
            SourceFile("src/old", b"remove\n"),
            SourceFile("src/mode", b"same\n"),
        )
    )
    result = _compare(
        source,
        ProposedChange("src/form", "after\n"),
        ProposedChange("src/old", None),
        ProposedChange("src/new", "added\n"),
        ProposedChange("src/mode", "same\n", mode="100755"),
    )
    payload = result["comparison"]
    assert result["comparisonDigest"] == digest(payload)
    modified, deleted, added, mode = payload["files"]
    assert modified["before"]["text"] == "before\n" and modified["after"]["text"] == "after\n"
    assert "-before\n+after\n" in modified["unifiedDiff"]
    assert deleted["after"] is None and "+++ /dev/null\n" in deleted["unifiedDiff"]
    assert added["before"] is None and "--- /dev/null\n" in added["unifiedDiff"]
    assert mode["before"]["mode"] == "100644" and mode["after"]["mode"] == "100755"
    assert "old mode 100644\nnew mode 100755\n" in mode["unifiedDiff"]
    assert payload["meaning"] == "ORIGINAL_SOURCE_COMPARISON_NOT_APPLICATION_OR_VERIFICATION"
    assert source.files[0].content == b"before\n"


def test_exact_line_endings_and_no_final_newline_are_not_silently_normalized() -> None:
    source = SourceSnapshot((SourceFile("src/form", b"old\r\nlast\vpart"),))
    file = _compare(source, ProposedChange("src/form", "new\nlast\vpart\n"))["comparison"]["files"][
        0
    ]
    assert file["before"]["text"] == "old\r\nlast\vpart"
    assert "-old\r\n" in file["unifiedDiff"]
    assert "-last\vpart\n\\ No newline at end of file\n" in file["unifiedDiff"]


@pytest.mark.parametrize(
    "fault", ["tree", "patch", "protected", "absent", "binary", "lines", "cancelled"]
)
def test_unavailable_source_is_refused_not_fabricated(fault: str) -> None:
    source = SourceSnapshot((SourceFile("src/form", b"original\n"),))
    change = ProposedChange("src/form", "after\n")
    if fault == "protected":
        change = ProposedChange("tests/protected.py", "pass\n")
    elif fault == "absent":
        change = ProposedChange("src/missing", None)
    elif fault == "binary":
        source = SourceSnapshot((SourceFile("src/form", b"\0"),))
    elif fault == "lines":
        source = SourceSnapshot((SourceFile("src/form", b"line\n" * 4001),))
    patch = _proposal(source, change)
    if fault == "tree":
        patch = replace(patch, base_source_digest="c" * 64)
    elif fault == "patch":
        patch = replace(patch, patch_digest="d" * 64)
    with pytest.raises(SnapshotRefused):
        compare_source(
            BoundCommitSource("b" * 40, source),
            patch=patch,
            application_paths=("src",),
            cancelled=lambda: fault == "cancelled",
        )


def test_command_requires_explicit_source_output_before_reading_repository_or_database() -> None:
    ident = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "--workspace-id",
                ident,
                "--project-id",
                ident,
                "--patch-id",
                ident,
                "--actor-id",
                ident,
                "--repository",
                "/missing-fixture-repository",
            ]
        )
    assert stopped.value.code == 2
