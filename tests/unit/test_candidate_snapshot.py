"""Immutable candidate preparation, not sandbox/AT/model execution evidence."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from accessforge_build_worker.snapshot import (
    MAX_FILE_BYTES,
    SnapshotRefused,
    SourceFile,
    SourceSnapshot,
    prepare_candidate,
    read_snapshot,
)
from accessforge_domain.authority import Approval, AuthorityError
from accessforge_domain.canonical import digest
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import ApprovalScope, PatchStatus
from accessforge_persistence.patches import PatchProposal, patch_digest

NOW = "2026-09-13T00:00:00Z"
BASE = SourceSnapshot(
    (
        SourceFile("src/form.html", b'<input id="email">'),
        SourceFile("src/app.py", b"print('owned reference app')\n"),
        SourceFile("tests/protected.py", b"assert True\n"),
    )
)
CHANGE = ProposedChange("src/form.html", '<label for="email">Email</label><input id="email">')


def _proposal(changes: tuple[ProposedChange, ...] = (CHANGE,)) -> PatchProposal:
    return PatchProposal(
        patch_id="patch-1",
        finding_id="finding-1",
        base_manifest_digest="b" * 64,
        base_source_digest=BASE.tree_digest,
        patch_digest=patch_digest(changes),
        changes=changes,
        verdicts=tuple((change.path, "ALLOWED") for change in changes),
        status=PatchStatus.APPROVED,
        approval_id="approval-1",
        proposed_by="author",
        rationale="label the email field",
        revision=2,
        created_at=NOW,
    )


def _approval(patch: PatchProposal) -> Approval:
    return Approval(
        approval_id="approval-1",
        scope=ApprovalScope.PATCH_APPLY,
        actor_id="owner",
        workspace_id="workspace-1",
        target_id=patch.patch_id,
        target_digest=patch.patch_digest,
        expected_revision=patch.revision,
        expires_at="2026-09-13T01:00:00Z",
    )


def test_tar_roundtrip_is_canonical_and_content_bound() -> None:
    loaded = read_snapshot(BASE.archive(), expected_tree_digest=BASE.tree_digest)
    assert loaded.tree_digest == BASE.tree_digest
    assert loaded.archive() == BASE.archive()
    assert loaded.archive_digest == hashlib.sha256(BASE.archive()).hexdigest()
    with pytest.raises(SnapshotRefused, match="persisted"):
        read_snapshot(BASE.archive(), expected_tree_digest="a" * 64)


def test_tree_digest_matches_the_existing_source_identity_contract() -> None:
    # Independent transcription of the v1 contract, including path-component ordering.
    snapshot = SourceSnapshot((SourceFile("a.txt", b"a"), SourceFile("a/z.py", b"b")))
    entries = [
        {"path": name, "kind": "file", "sha256": hashlib.sha256(content).hexdigest(), "size": 1}
        for name, content in (("a/z.py", b"b"), ("a.txt", b"a"))
    ]
    assert snapshot.tree_digest == digest({"entries": entries})


def test_modes_are_bound_in_archive_identity_even_when_content_digest_is_equal() -> None:
    plain = SourceSnapshot((SourceFile("src/script.py", b"pass"),))
    executable = SourceSnapshot((SourceFile("src/script.py", b"pass", 0o755),))
    assert plain.tree_digest == executable.tree_digest  # Existing module-05 v1 limitation.
    assert plain.archive_digest != executable.archive_digest


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/etc/passwd",
        "src/../../escape",
        "src//form",
        "./src/form",
        "src/./form",
        "src\\form",
        ".git/config",
        ".GIT/config",
        "C:/escape",
        "src/x:stream",
        "src/line\nbreak",
        "src/e\u0301",
        ".",
        "src/\udcff",
    ],
)
def test_noncanonical_or_escaping_source_paths_are_refused(name: str) -> None:
    with pytest.raises(SnapshotRefused):
        SourceFile(name, b"not executed")


@pytest.mark.parametrize("mode", [0o777, 0o4755, 0o2644, 0o000])
def test_privileged_or_unsupported_source_modes_are_refused(mode: int) -> None:
    with pytest.raises(SnapshotRefused, match="modes"):
        SourceFile("src/form", b"content", mode)


@pytest.mark.parametrize(
    "names",
    [("src/x", "src/x"), ("src/x", "src/X"), ("src", "src/x"), ("src/x", "SRC/y")],
)
def test_conflicting_file_and_directory_names_are_refused(names: tuple[str, str]) -> None:
    with pytest.raises(SnapshotRefused):
        SourceSnapshot(tuple(SourceFile(name, b"x") for name in names))


def _archive(member: tarfile.TarInfo, content: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_links_and_special_members_never_reach_an_extractor(kind: bytes, tmp_path: Path) -> None:
    member = tarfile.TarInfo("src/escape")
    member.type = kind
    member.linkname = str(tmp_path / "host-canary")
    with pytest.raises(SnapshotRefused, match="special"):
        read_snapshot(_archive(member), expected_tree_digest=BASE.tree_digest)
    assert list(tmp_path.iterdir()) == []


def test_claimed_large_member_is_refused_before_reading_its_bytes() -> None:
    member = tarfile.TarInfo("src/bomb")
    member.size = MAX_FILE_BYTES + 1
    with pytest.raises(SnapshotRefused, match="byte limit"):
        read_snapshot(member.tobuf() + b"\0" * 1024, expected_tree_digest=BASE.tree_digest)


@pytest.mark.parametrize("payload", [b"", b"not a tar", BASE.archive()[:600]])
def test_truncated_and_malformed_archives_are_refused(payload: bytes) -> None:
    with pytest.raises(SnapshotRefused):
        read_snapshot(payload, expected_tree_digest=BASE.tree_digest)


def test_candidate_changes_only_exact_approved_bytes_and_preserves_the_base() -> None:
    patch = _proposal()
    before = BASE.archive()
    candidate = prepare_candidate(
        BASE,
        patch=patch,
        approval=_approval(patch),
        workspace_id="workspace-1",
        application_paths=("src",),
        now=NOW,
    )
    assert BASE.archive() == before
    assert candidate.base_tree_digest == BASE.tree_digest
    assert candidate.base_archive_digest == BASE.archive_digest
    assert candidate.patch_digest == patch.patch_digest
    assert {file.path: file.content for file in candidate.source.files} == {
        "src/form.html": CHANGE.content.encode() if CHANGE.content is not None else b"",
        "src/app.py": b"print('owned reference app')\n",
        "tests/protected.py": b"assert True\n",
    }
    assert candidate.source.tree_digest != BASE.tree_digest
    assert not hasattr(candidate, "verified")
    assert not hasattr(candidate, "built")


@pytest.mark.parametrize(
    "axis", ["revoked", "expired", "scope", "workspace", "revision", "digest", "target"]
)
def test_every_approval_boundary_is_checked(axis: str) -> None:
    patch = _proposal()
    approval = _approval(patch)
    if axis == "revoked":
        approval = replace(approval, revoked=True)
    elif axis == "expired":
        approval = replace(approval, expires_at=NOW)
    elif axis == "scope":
        approval = replace(approval, scope=ApprovalScope.RUN_EFFECTS)
    elif axis == "workspace":
        approval = replace(approval, workspace_id="other")
    elif axis == "revision":
        approval = replace(approval, expected_revision=1)
    elif axis == "target":
        approval = replace(approval, target_id="another-patch")
    else:
        approval = replace(approval, target_digest="f" * 64)
    with pytest.raises(AuthorityError):
        prepare_candidate(
            BASE,
            patch=patch,
            approval=approval,
            workspace_id="workspace-1",
            application_paths=("src",),
            now=NOW,
        )


@pytest.mark.parametrize(
    "changes",
    [
        (ProposedChange("tests/protected.py", "assert False"),),
        (ProposedChange("src/validation.py", "pass"),),
        (ProposedChange("src/package.json", "{}"),),
        (ProposedChange("src/escape", "/etc/passwd", mode="120000"),),
        (ProposedChange("../escape", "x"),),
        (ProposedChange("outside/file", "x"),),
        (CHANGE, CHANGE),
        (ProposedChange("src/missing", None),),
        (),
    ],
)
def test_policy_is_rechecked_even_if_the_stored_rulings_say_allowed(
    changes: tuple[ProposedChange, ...],
) -> None:
    patch = _proposal(changes)
    with pytest.raises(SnapshotRefused):
        prepare_candidate(
            BASE,
            patch=patch,
            approval=_approval(patch),
            workspace_id="workspace-1",
            application_paths=("src",),
            now=NOW,
        )


@pytest.mark.parametrize("axis", ["base", "patch", "approval_id", "status", "surface"])
def test_stale_or_unconfigured_candidate_is_refused(axis: str) -> None:
    patch = _proposal()
    approval = _approval(patch)
    if axis == "base":
        patch = replace(patch, base_source_digest="f" * 64)
    elif axis == "patch":
        patch = replace(patch, changes=(ProposedChange("src/form.html", "different"),))
    elif axis == "approval_id":
        patch = replace(patch, approval_id="unrelated")
    elif axis == "status":
        patch = replace(patch, status=PatchStatus.BUILDING)
    with pytest.raises(SnapshotRefused):
        prepare_candidate(
            BASE,
            patch=patch,
            approval=approval,
            workspace_id="workspace-1",
            application_paths=() if axis == "surface" else ("src",),
            now=NOW,
        )


def test_duplicate_archive_members_are_refused_not_silently_overwritten() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for _ in range(2):
            member = tarfile.TarInfo("src/form.html")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(SnapshotRefused, match="duplicate"):
        read_snapshot(buffer.getvalue(), expected_tree_digest=BASE.tree_digest)


def test_compressed_archives_cannot_expand_beyond_the_input_bound() -> None:
    with pytest.raises(SnapshotRefused, match="uncompressed"):
        read_snapshot(gzip.compress(BASE.archive()), expected_tree_digest=BASE.tree_digest)


def test_archive_member_and_total_byte_budgets_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import accessforge_build_worker.snapshot as snapshots

    payload = BASE.archive()
    with monkeypatch.context() as bounds:
        bounds.setattr(snapshots, "MAX_MEMBERS", 2)
        with pytest.raises(SnapshotRefused, match="member limit"):
            read_snapshot(payload, expected_tree_digest=BASE.tree_digest)
    with monkeypatch.context() as bounds:
        bounds.setattr(snapshots, "MAX_SOURCE_BYTES", 1)
        with pytest.raises(SnapshotRefused, match="expanded byte limit"):
            read_snapshot(payload, expected_tree_digest=BASE.tree_digest)
    with monkeypatch.context() as bounds:
        bounds.setattr(snapshots, "MAX_ARCHIVE_BYTES", len(payload) - 1)
        with pytest.raises(SnapshotRefused, match="archive exceeds"):
            read_snapshot(payload, expected_tree_digest=BASE.tree_digest)


def test_add_delete_and_executable_mode_are_applied_without_touching_protected_files() -> None:
    changes = (
        ProposedChange("src/app.py", None),
        ProposedChange("src/cli.py", "print('candidate')", mode="100755"),
    )
    patch = _proposal(changes)
    candidate = prepare_candidate(
        BASE,
        patch=patch,
        approval=_approval(patch),
        workspace_id="workspace-1",
        application_paths=("src",),
        now=NOW,
    )
    files = {file.path: file for file in candidate.source.files}
    assert "src/app.py" not in files
    assert files["src/cli.py"].mode == 0o755
    assert files["src/cli.py"].content == b"print('candidate')"
    assert files["tests/protected.py"].content == b"assert True\n"
    assert {file.path for file in BASE.files} == {
        "src/app.py",
        "src/form.html",
        "tests/protected.py",
    }
