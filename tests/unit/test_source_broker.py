"""Real local Git object recovery with owned synthetic repositories, no source execution."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from accessforge_build_worker.comparison import compare_source
from accessforge_build_worker.process import CommandStopped
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import _decode_batch, read_committed_source
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import PatchStatus
from accessforge_persistence.patches import PatchProposal, patch_digest
from accessforge_persistence.source_intake import SourceIdentity


def _git(repository: Path, *args: str, data: bytes | None = None) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result = subprocess.run(  # noqa: S603 - owned disposable repository fixture construction.
        [executable, "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args],
        input=data,
        capture_output=True,
        check=True,
        timeout=10,
        env={"PATH": os.defpath, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return result.stdout.decode().strip()


@pytest.fixture()
def committed(tmp_path: Path) -> tuple[Path, SourceIdentity, SourceSnapshot]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--object-format=sha1")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.test")
    source = SourceSnapshot(
        (
            SourceFile("src/app.txt", b"original\n"),
            SourceFile("run.sh", b"echo must-not-run\n", 0o755),
            SourceFile(".gitattributes", b"src/app.txt export-ignore\nrun.sh export-subst\n"),
        )
    )
    for file in source.files:
        target = repository / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.content)
        target.chmod(file.mode)
    _git(repository, "add", ".")
    _git(repository, "commit", "--no-gpg-sign", "-m", "owned fixture")
    oid = _git(repository, "rev-parse", "HEAD")
    return repository, SourceIdentity(oid, source.tree_digest, False), source


def test_committed_bytes_and_modes_ignore_dirty_checkout_and_archive_attributes(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, expected = committed
    (repository / "src/app.txt").write_text("dirty replacement")
    (repository / "run.sh").chmod(0o644)
    (repository / "untracked").write_text("not committed")
    result = read_committed_source(repository, identity=identity)
    assert result.commit_sha == identity.commit_sha
    assert result.source.archive() == expected.archive()
    assert (repository / "src/app.txt").read_text() == "dirty replacement"
    assert (repository / "untracked").exists()


def test_comparison_uses_original_committed_source_and_preserves_dirty_checkout(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, original = committed
    (repository / "src/app.txt").write_text("dirty user edits")
    changes = (ProposedChange("src/app.txt", "proposed repair\n"),)
    patch = PatchProposal(
        patch_id="patch-1",
        finding_id="finding-1",
        base_manifest_digest="a" * 64,
        base_source_digest=identity.tree_digest,
        patch_digest=patch_digest(changes),
        changes=changes,
        verdicts=(("src/app.txt", "ALLOWED"),),
        status=PatchStatus.PROPOSED,
        approval_id=None,
        proposed_by="author",
        rationale="repair the task",
        revision=1,
        created_at="2026-09-13T00:00:00Z",
    )
    comparison = compare_source(
        read_committed_source(repository, identity=identity),
        patch=patch,
        application_paths=("src",),
    )["comparison"]
    assert comparison["baseCommitSha"] == identity.commit_sha
    assert comparison["baseArchiveDigest"] == original.archive_digest
    assert comparison["files"][0]["before"]["text"] == "original\n"
    assert "-original\n+proposed repair\n" in comparison["files"][0]["unifiedDiff"]
    assert (repository / "src/app.txt").read_text() == "dirty user edits"
    assert patch.status is PatchStatus.PROPOSED and patch.approval_id is None


def test_replacement_refs_and_inherited_git_directory_cannot_substitute_the_commit(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, expected = committed
    (repository / "src/app.txt").write_text("replacement ref content")
    _git(repository, "add", ".")
    _git(repository, "commit", "--no-gpg-sign", "-m", "replacement")
    replacement = _git(repository, "rev-parse", "HEAD")
    _git(repository, "replace", identity.commit_sha, replacement)
    monkeypatch.setenv("GIT_DIR", "/does-not-exist")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "broken")
    result = read_committed_source(repository, identity=identity)
    assert result.source.archive() == expected.archive()


def test_changed_executable_bit_has_distinct_commit_bound_archive(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, expected = committed
    _git(repository, "update-index", "--chmod=-x", "run.sh")
    _git(repository, "commit", "--no-gpg-sign", "-m", "mode only")
    changed = replace(identity, commit_sha=_git(repository, "rev-parse", "HEAD"))
    original = read_committed_source(repository, identity=identity)
    candidate = read_committed_source(repository, identity=changed)
    assert original.source.tree_digest == candidate.source.tree_digest == expected.tree_digest
    assert original.archive_digest != candidate.archive_digest


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_links_and_submodules_are_not_silently_omitted(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
    mode: str,
) -> None:
    repository, identity, _ = committed
    oid = (
        identity.commit_sha
        if mode == "160000"
        else _git(repository, "hash-object", "-w", "--stdin", data=b"/etc/passwd")
    )
    _git(repository, "update-index", "--add", "--cacheinfo", f"{mode},{oid},escape")
    _git(repository, "commit", "--no-gpg-sign", "-m", "unsupported entry")
    identity = replace(identity, commit_sha=_git(repository, "rev-parse", "HEAD"))
    with pytest.raises(SnapshotRefused, match="links, submodules"):
        read_committed_source(repository, identity=identity)


@pytest.mark.parametrize("dirty", [True, False])
def test_dirty_identity_never_falls_back_to_clean_commit_bytes(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
    dirty: bool,
) -> None:
    repository, identity, _ = committed
    identity = replace(identity, dirty=dirty, dirty_paths=("src/app.txt",))
    with pytest.raises(SnapshotRefused, match="dirty source"):
        read_committed_source(repository, identity=identity)


def test_persisted_content_mismatch_refuses_the_binding(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, _ = committed
    with pytest.raises(SnapshotRefused, match="persisted source-tree"):
        read_committed_source(repository, identity=replace(identity, tree_digest="a" * 64))


@pytest.mark.parametrize("revision", ["HEAD", "--help", "a" * 39, "a" * 41])
def test_refs_and_noncanonical_commit_ids_are_refused(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
    revision: str,
) -> None:
    repository, identity, _ = committed
    with pytest.raises(SnapshotRefused, match="immutable"):
        read_committed_source(repository, identity=replace(identity, commit_sha=revision))


def test_filters_are_never_executed(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, expected = committed
    canary = repository / "filter-ran"
    _git(repository, "config", "filter.host.smudge", "touch filter-ran")
    (repository / ".gitattributes").write_text("* filter=host\n")
    assert (
        read_committed_source(repository, identity=identity).source.archive() == expected.archive()
    )
    assert not canary.exists()


def test_corrupted_blob_under_the_original_address_is_refused(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, _ = committed
    oid = _git(repository, "rev-parse", "HEAD:src/app.txt")
    target = repository / ".git/objects" / oid[:2] / oid[2:]
    target.chmod(0o644)
    target.write_bytes(zlib.compress(b"blob 6\0forged"))
    with pytest.raises(SnapshotRefused, match="content address"):
        read_committed_source(repository, identity=identity)


def test_missing_commit_does_not_fetch(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, _ = committed
    _git(repository, "config", "remote.origin.promisor", "true")
    _git(repository, "config", "remote.origin.url", "ext::touch fetch-ran")
    with pytest.raises(SnapshotRefused, match="missing|read failed"):
        read_committed_source(repository, identity=replace(identity, commit_sha="a" * 40))
    assert not (repository / "fetch-ran").exists()


def test_cancellation_prevents_source_reads(
    committed: tuple[Path, SourceIdentity, SourceSnapshot],
) -> None:
    repository, identity, _ = committed
    with pytest.raises(CommandStopped, match="cancelled"):
        read_committed_source(repository, identity=identity, cancelled=lambda: True)


@pytest.mark.parametrize("suffix", [b"", b"\nextra", b"x"])
def test_batch_response_must_have_exact_framing(suffix: bytes) -> None:
    oid = hashlib.sha1(b"blob 1\0a", usedforsecurity=False).hexdigest()
    with pytest.raises(SnapshotRefused, match="truncated|trailing"):
        _decode_batch(f"{oid} blob 1\n".encode() + b"a" + suffix, ((oid, "blob"),))
