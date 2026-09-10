"""Source identity, safe intake, and sealing — against a real git repository and real PostgreSQL.

The theme is that a mutable reference is never an identity. A force-pushed branch keeps its name and
changes its bytes; a dirty checkout shares a commit's name and not its contents; the same deployment
URL can serve a different artifact tomorrow. Each of those is exercised here rather than argued
about.

Requirements: FR-001, FR-002, FR-010, FR-014. Invariants: INV-03, INV-07, INV-08.
"""

from __future__ import annotations

import subprocess
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.origins import normalize_origin
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    projects,
    source_intake,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xB0))
WS_OTHER = str(uuid.UUID(int=0xB1))
OWNER = str(uuid.UUID(int=0xB2))
NOW = "2026-09-10T12:00:00Z"
LATER = "2026-09-11T12:00:00Z"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(repo),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return out.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit."""
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


@pytest.fixture()
def db(test_database_url: str) -> Iterator[tuple[str, str]]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace, app_user RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (OWNER, "o@example.test"))
    with workspace_connection(test_database_url, WS) as conn:
        # The authorizing user must hold a live membership here. This fixture previously recorded an
        # account with no membership, which the database accepted because the column only references
        # `app_user` -- so the authorization named somebody this workspace could not name.
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'OWNER')",
            (WS, OWNER),
        )
        project_id = projects.create_project(
            conn,
            workspace_id=WS,
            name="reference",
            repository_url="file:///local/reference",
            repository_authorized_by=OWNER,
        )
    yield test_database_url, project_id


def _spec(name: str = "staging", **over: object) -> projects.EnvironmentSpec:
    defaults: dict[str, object] = {
        "name": name,
        "allowed_origins": frozenset({normalize_origin("http://127.0.0.1:8081")}),
        "fixture_reset_strategy": "test-reset-endpoint",
        "observer_credential_ref": "secretref://observer",
        "reset_credential_ref": "secretref://reset",
        "permitted_effects": frozenset({"FIXTURE_SUBMIT"}),
        "expires_at": LATER,
    }
    return projects.EnvironmentSpec(**{**defaults, **over})  # type: ignore[arg-type]


# --- source identity ---------------------------------------------------------------------------


def test_a_clean_checkout_resolves_to_a_reproducible_identity(repo: Path) -> None:
    identity = source_intake.resolve_source(repo)
    assert len(identity.commit_sha) == 40
    assert len(identity.tree_digest) == 64
    assert not identity.dirty
    assert identity.is_reproducible_from_the_commit
    source_intake.assert_reproducible(identity)  # allowed-path control


def test_a_dirty_checkout_is_recorded_as_dirty_and_refuses_a_reproducibility_claim(
    repo: Path,
) -> None:
    """A dirty tree shares the commit's name but not its bytes."""
    clean = source_intake.resolve_source(repo)
    (repo / "app.py").write_text("print('edited')\n", encoding="utf-8")
    dirty = source_intake.resolve_source(repo)

    assert dirty.commit_sha == clean.commit_sha, "the commit is unchanged"
    assert dirty.tree_digest != clean.tree_digest, "the bytes are not"
    assert dirty.dirty
    assert "app.py" in dirty.dirty_paths
    assert not dirty.is_reproducible_from_the_commit

    with pytest.raises(source_intake.ReproducibilityRefused, match="differs from commit"):
        source_intake.assert_reproducible(dirty)


def test_an_untracked_file_also_makes_a_tree_dirty(repo: Path) -> None:
    """An untracked file changes what a run actually sees, so it counts."""
    (repo / "scratch.txt").write_text("notes\n", encoding="utf-8")
    identity = source_intake.resolve_source(repo)
    assert identity.dirty
    assert "scratch.txt" in identity.dirty_paths


def test_a_force_pushed_ref_keeps_its_name_and_changes_its_identity(repo: Path) -> None:
    """The case that makes a branch name useless as an identity.

    `main` is resolved to a commit the moment it is read, so a later rewrite produces a different
    identity rather than silently changing what an existing one means.
    """
    before = source_intake.resolve_source(repo, revision="main")

    (repo / "app.py").write_text("print('rewritten')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")  # a force push, locally
    after = source_intake.resolve_source(repo, revision="main")

    assert before.commit_sha != after.commit_sha
    assert before.tree_digest != after.tree_digest
    assert not before.dirty and not after.dirty, "both are clean; only the identity moved"


def test_a_rename_changes_the_tree_digest_even_with_identical_content(repo: Path) -> None:
    """Paths are part of the identity: a rename is a different tree."""
    before = source_intake.resolve_source(repo)
    (repo / "renamed.py").write_text(
        (repo / "app.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / "app.py").unlink()
    after = source_intake.resolve_source(repo)
    assert before.tree_digest != after.tree_digest


def test_a_symlink_is_recorded_by_target_and_not_followed(repo: Path, tmp_path: Path) -> None:
    """Following a link would let a file outside the tree contribute to its identity."""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (repo / "link").symlink_to(outside)

    identity = source_intake.resolve_source(repo)
    assert identity.dirty  # the link is untracked

    # Changing the *target's content* must not change the tree digest, because the link's target
    # path is what was recorded.
    before = identity.tree_digest
    outside.write_text("different secret\n", encoding="utf-8")
    assert source_intake.resolve_source(repo).tree_digest == before


def test_a_non_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(source_intake.SourceIntakeError, match="not a git repository"):
        source_intake.resolve_source(tmp_path)


def test_an_unknown_revision_is_refused(repo: Path) -> None:
    with pytest.raises(source_intake.SourceIntakeError):
        source_intake.resolve_source(repo, revision="no-such-ref")


# --- archive intake safety ---------------------------------------------------------------------


def _tar_with(tmp_path: Path, build: object) -> Path:
    archive = tmp_path / "payload.tar"
    with tarfile.open(archive, "w") as tar:
        build(tar)  # type: ignore[operator]
    return archive


def test_a_benign_archive_extracts(tmp_path: Path) -> None:
    # Allowed-path control: a safe extractor that refused everything would be useless.
    content = tmp_path / "f.txt"
    content.write_text("hello\n", encoding="utf-8")
    archive = _tar_with(tmp_path, lambda tar: tar.add(content, arcname="dir/f.txt"))
    destination = tmp_path / "out"
    assert source_intake.extract_archive_safely(archive, destination) == 1
    assert (destination / "dir" / "f.txt").read_text(encoding="utf-8") == "hello\n"


def test_an_absolute_path_member_is_refused(tmp_path: Path) -> None:
    """The member is built by hand on purpose.

    `tar.add(..., arcname="/etc/x")` silently normalises the leading slash away, so the obvious
    version of this test exercises tarfile's normalisation rather than our check. A real malicious
    archive is not written by tarfile.
    """
    archive = tmp_path / "abs.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("/etc/cron.d/evil")
        info.size = 0
        tar.addfile(info)
    with pytest.raises(source_intake.SourceIntakeError, match="absolute path"):
        source_intake.extract_archive_safely(archive, tmp_path / "out")


def test_a_traversing_member_is_refused(tmp_path: Path) -> None:
    content = tmp_path / "f.txt"
    content.write_text("x\n", encoding="utf-8")
    archive = _tar_with(tmp_path, lambda tar: tar.add(content, arcname="../../escaped.txt"))
    with pytest.raises(source_intake.SourceIntakeError, match="traverses outside"):
        source_intake.extract_archive_safely(archive, tmp_path / "out")


def test_a_symlink_member_is_refused(tmp_path: Path) -> None:
    """A link written first can be followed by a later member to write anywhere."""
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(source_intake.SourceIntakeError, match="is a link"):
        source_intake.extract_archive_safely(archive, tmp_path / "out")


def test_a_hard_link_member_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "hard.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("hard")
        info.type = tarfile.LNKTYPE
        info.linkname = "etc/passwd"
        tar.addfile(info)
    with pytest.raises(source_intake.SourceIntakeError, match="is a link"):
        source_intake.extract_archive_safely(archive, tmp_path / "out")


def test_a_device_member_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "dev.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("dev/null")
        info.type = tarfile.CHRTYPE
        tar.addfile(info)
    with pytest.raises(source_intake.SourceIntakeError, match="neither a file nor a directory"):
        source_intake.extract_archive_safely(archive, tmp_path / "out")


def test_nothing_outside_the_destination_is_written_when_a_member_is_refused(
    tmp_path: Path,
) -> None:
    """A refusal must not leave a partial escape behind."""
    content = tmp_path / "f.txt"
    content.write_text("x\n", encoding="utf-8")
    archive = tmp_path / "mixed.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(content, arcname="ok.txt")
        tar.add(content, arcname="../escaped.txt")

    destination = tmp_path / "out"
    with pytest.raises(source_intake.SourceIntakeError):
        source_intake.extract_archive_safely(archive, destination)
    assert not (tmp_path / "escaped.txt").exists()


# --- environments ------------------------------------------------------------------------------


def test_a_repository_without_an_authorizing_user_is_refused(db: tuple[str, str]) -> None:
    """Reachability is not consent."""
    url, _ = db
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="consent"),
    ):
        projects.create_project(
            conn, workspace_id=WS, name="unauthorized", repository_url="https://github.test/x"
        )


def test_the_observer_and_reset_credentials_must_differ(db: tuple[str, str]) -> None:
    """One identity that can both reset state and attest to it is not an independent observer."""
    url, project_id = db
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="independent observer"),
    ):
        projects.register_environment(
            conn,
            workspace_id=WS,
            project_id=project_id,
            spec=_spec(
                observer_credential_ref="secretref://same", reset_credential_ref="secretref://same"
            ),
            authorized_by=OWNER,
        )


def test_an_environment_must_declare_an_origin(db: tuple[str, str]) -> None:
    url, project_id = db
    with workspace_connection(url, WS) as conn, pytest.raises(projects.ProjectError):
        projects.register_environment(
            conn,
            workspace_id=WS,
            project_id=project_id,
            spec=_spec(allowed_origins=frozenset()),
            authorized_by=OWNER,
        )


def test_the_config_digest_excludes_credential_values_but_includes_their_references(
    db: tuple[str, str],
) -> None:
    """Swapping which credential profile is used is a configuration change; the values are never
    here."""
    a = _spec(observer_credential_ref="secretref://observer-a")
    b = _spec(observer_credential_ref="secretref://observer-b")
    assert a.config_digest() != b.config_digest()
    assert _spec().config_digest() == _spec().config_digest()


def test_a_revoked_or_expired_environment_cannot_be_used(db: tuple[str, str]) -> None:
    url, project_id = db
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
        projects.assert_environment_usable(conn, environment_id=env, now=NOW)  # control

    with workspace_connection(url, WS) as conn:
        conn.execute("UPDATE environment_manifest SET revoked_at = now() WHERE id = %s", (env,))
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="revoked"),
    ):
        projects.assert_environment_usable(conn, environment_id=env, now=NOW)


def test_an_expired_environment_cannot_be_used(db: tuple[str, str]) -> None:
    url, project_id = db
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="expired"),
    ):
        projects.assert_environment_usable(conn, environment_id=env, now="2026-09-12T00:00:00Z")


def test_superseding_keeps_the_old_manifest_intact(db: tuple[str, str]) -> None:
    """Append-only: evidence citing the old identity keeps meaning what it meant."""
    url, project_id = db
    with workspace_connection(url, WS) as conn:
        old = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
    with workspace_connection(url, WS) as conn:
        new = projects.supersede_environment(
            conn,
            workspace_id=WS,
            old_environment_id=old,
            spec=_spec(fixture_reset_strategy="truncate-and-seed"),
            authorized_by=OWNER,
        )
    with workspace_connection(url, WS) as conn:
        row = conn.execute(
            "SELECT superseded_by, fixture_reset_strategy FROM environment_manifest WHERE id = %s",
            (old,),
        ).fetchone()
        assert row is not None
        assert str(row["superseded_by"]) == new
        assert row["fixture_reset_strategy"] == "test-reset-endpoint", "the old row is unchanged"

        with pytest.raises(projects.ProjectError, match="superseded"):
            projects.assert_environment_usable(conn, environment_id=old, now=NOW)
        projects.assert_environment_usable(conn, environment_id=new, now=NOW)  # control


# --- sealing -----------------------------------------------------------------------------------


def _seal_inputs() -> projects.SealInputs:
    return projects.SealInputs(
        journey_digest=digest({"journey": 1}),
        assertion_set_digest=digest({"assertions": 1}),
        fixture_digest=digest({"fixtures": 1}),
        runner_profile_digest=digest({"runner": 1}),
        navigator_policy_digest=digest({"policy": 1}),
        evaluator_version="1.0.0",
        model_config_digest=digest({"model": 1}),
    )


def _seal(
    url: str, project_id: str, repo: Path, *, observable: bool = True
) -> tuple[str, str, str]:
    identity = source_intake.resolve_source(repo)
    artifact_digest = digest({"artifact": identity.tree_digest})
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
        snapshot = projects.record_source_snapshot(
            conn,
            workspace_id=WS,
            project_id=project_id,
            identity=identity,
            requested_revision="main",
        )
        artifact = projects.record_build_artifact(
            conn,
            workspace_id=WS,
            project_id=project_id,
            source_snapshot_id=snapshot,
            artifact_digest=artifact_digest,
            identity_observable=observable,
        )
        seal = projects.seal_run(
            conn,
            workspace_id=WS,
            project_id=project_id,
            source_snapshot_id=snapshot,
            build_artifact_id=artifact,
            environment_manifest_id=env,
            inputs=_seal_inputs(),
            now=NOW,
        )
    return seal.sealed_manifest_id, seal.manifest_digest, artifact_digest


def test_sealing_produces_a_deterministic_manifest_digest(db: tuple[str, str], repo: Path) -> None:
    url, project_id = db
    _, first, _ = _seal(url, project_id, repo)
    _, second, _ = _seal(url, project_id, repo)
    assert first == second, "identical inputs must seal to the same digest"
    assert len(first) == 64
    # Deliberately not unique in the schema: a baseline and its candidate must be able to share an
    # input digest, which is how INV-04 shows they differ only by the approved patch.


def test_changing_any_sealed_input_changes_the_digest(db: tuple[str, str], repo: Path) -> None:
    url, project_id = db
    _, baseline, _ = _seal(url, project_id, repo)
    (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v2")
    _, changed, _ = _seal(url, project_id, repo)
    assert baseline != changed


def test_a_sealed_manifest_is_immutable(db: tuple[str, str], repo: Path) -> None:
    """A changed input requires a new seal, never an amendment."""
    url, project_id = db
    sealed_id, _, _ = _seal(url, project_id, repo)
    with pytest.raises(psycopg.errors.IntegrityError, match="immutable"):
        with workspace_connection(url, WS) as conn:
            conn.execute(
                "UPDATE sealed_manifest SET evaluator_version = '9.9.9' WHERE id = %s", (sealed_id,)
            )


def test_revalidation_passes_when_nothing_changed(db: tuple[str, str], repo: Path) -> None:
    # Allowed-path control.
    url, project_id = db
    sealed_id, _, artifact_digest = _seal(url, project_id, repo)
    with workspace_connection(url, WS) as conn:
        projects.revalidate_before_dispatch(
            conn,
            sealed_manifest_id=sealed_id,
            observed_source=source_intake.resolve_source(repo),
            observed_artifact_digest=artifact_digest,
            now=NOW,
        )


def test_a_force_push_between_sealing_and_dispatch_stops_the_run(
    db: tuple[str, str], repo: Path
) -> None:
    url, project_id = db
    sealed_id, _, artifact_digest = _seal(url, project_id, repo)

    (repo / "app.py").write_text("print('rewritten')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")

    with workspace_connection(url, WS) as conn:
        with pytest.raises(projects.IdentityChanged, match="source commit changed"):
            projects.revalidate_before_dispatch(
                conn,
                sealed_manifest_id=sealed_id,
                observed_source=source_intake.resolve_source(repo),
                observed_artifact_digest=artifact_digest,
                now=NOW,
            )


def test_an_edited_working_tree_between_sealing_and_dispatch_stops_the_run(
    db: tuple[str, str], repo: Path
) -> None:
    """Same commit, different bytes."""
    url, project_id = db
    sealed_id, _, artifact_digest = _seal(url, project_id, repo)
    (repo / "app.py").write_text("print('edited')\n", encoding="utf-8")

    with workspace_connection(url, WS) as conn:
        with pytest.raises(projects.IdentityChanged, match="tree changed"):
            projects.revalidate_before_dispatch(
                conn,
                sealed_manifest_id=sealed_id,
                observed_source=source_intake.resolve_source(repo),
                observed_artifact_digest=artifact_digest,
                now=NOW,
            )


def test_artifact_substitution_under_the_same_url_stops_the_run(
    db: tuple[str, str], repo: Path
) -> None:
    """The same deployment URL serving different bytes."""
    url, project_id = db
    sealed_id, _, _ = _seal(url, project_id, repo)
    with workspace_connection(url, WS) as conn:
        with pytest.raises(projects.IdentityChanged, match="artifact changed"):
            projects.revalidate_before_dispatch(
                conn,
                sealed_manifest_id=sealed_id,
                observed_source=source_intake.resolve_source(repo),
                observed_artifact_digest=digest({"artifact": "substituted"}),
                now=NOW,
            )


def test_an_environment_revoked_after_sealing_stops_the_run(
    db: tuple[str, str], repo: Path
) -> None:
    """Authorization is rechecked at dispatch even when every byte still matches."""
    url, project_id = db
    sealed_id, _, artifact_digest = _seal(url, project_id, repo)
    with workspace_connection(url, WS) as conn:
        conn.execute(
            """
            UPDATE environment_manifest SET revoked_at = now()
            WHERE id = (SELECT environment_manifest_id FROM sealed_manifest WHERE id = %s)
            """,
            (sealed_id,),
        )
    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="revoked"),
    ):
        projects.revalidate_before_dispatch(
            conn,
            sealed_manifest_id=sealed_id,
            observed_source=source_intake.resolve_source(repo),
            observed_artifact_digest=artifact_digest,
            now=NOW,
        )


def test_sealing_against_a_revoked_environment_is_refused(db: tuple[str, str], repo: Path) -> None:
    url, project_id = db
    identity = source_intake.resolve_source(repo)
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
        snapshot = projects.record_source_snapshot(
            conn,
            workspace_id=WS,
            project_id=project_id,
            identity=identity,
            requested_revision="main",
        )
        artifact = projects.record_build_artifact(
            conn,
            workspace_id=WS,
            project_id=project_id,
            source_snapshot_id=snapshot,
            artifact_digest=digest({"a": 1}),
            identity_observable=True,
        )
        conn.execute("UPDATE environment_manifest SET revoked_at = now() WHERE id = %s", (env,))

    with (
        workspace_connection(url, WS) as conn,
        pytest.raises(projects.ProjectError, match="revoked"),
    ):
        projects.seal_run(
            conn,
            workspace_id=WS,
            project_id=project_id,
            source_snapshot_id=snapshot,
            build_artifact_id=artifact,
            environment_manifest_id=env,
            inputs=_seal_inputs(),
            now=NOW,
        )


# --- observability of build identity -----------------------------------------------------------


def test_an_unobservable_deployment_cannot_claim_verified_provenance(
    db: tuple[str, str], repo: Path
) -> None:
    """The limitation stays visible rather than being filled with an invented digest."""
    url, project_id = db
    _seal(url, project_id, repo, observable=False)
    with workspace_connection(url, WS) as conn:
        env_id = str(
            conn.execute("SELECT environment_manifest_id FROM sealed_manifest LIMIT 1").fetchone()[
                "environment_manifest_id"
            ]  # type: ignore[index]
        )
        summary = projects.capability_summary(conn, environment_id=env_id)
    assert summary["buildIdentityObservable"] is False


def test_build_identity_observability_is_unknown_before_anything_is_sealed(
    db: tuple[str, str],
) -> None:
    """Unknown, not optimistically True."""
    url, project_id = db
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
        summary = projects.capability_summary(conn, environment_id=env)
    assert summary["buildIdentityObservable"] is None


def test_the_capability_summary_never_exposes_credential_references(
    db: tuple[str, str],
) -> None:
    """A journey author needs to know a reset strategy exists, not which credential performs it."""
    url, project_id = db
    with workspace_connection(url, WS) as conn:
        env = projects.register_environment(
            conn, workspace_id=WS, project_id=project_id, spec=_spec(), authorized_by=OWNER
        )
        summary = projects.capability_summary(conn, environment_id=env)
    rendered = repr(summary)
    assert "secretref://observer" not in rendered
    assert "secretref://reset" not in rendered
    assert "credential" not in rendered.lower()


# --- isolation ---------------------------------------------------------------------------------


def test_projects_environments_and_seals_are_workspace_isolated(
    db: tuple[str, str], repo: Path
) -> None:
    url, project_id = db
    _seal(url, project_id, repo)
    with workspace_connection(url, WS_OTHER) as conn:
        for table in (
            "project",
            "environment_manifest",
            "source_snapshot",
            "build_artifact",
            "sealed_manifest",
        ):
            assert conn.execute(f"SELECT 1 FROM {table}").fetchall() == [], table  # noqa: S608


def test_an_environment_cannot_be_registered_against_another_workspaces_project(
    db: tuple[str, str],
) -> None:
    """The composite foreign key refuses it even though the project id is known."""
    url, project_id = db
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.InsufficientPrivilege)):
        with workspace_connection(url, WS_OTHER) as conn:
            projects.register_environment(
                conn,
                workspace_id=WS_OTHER,
                project_id=project_id,  # belongs to WS
                spec=_spec(),
                authorized_by=OWNER,
            )
