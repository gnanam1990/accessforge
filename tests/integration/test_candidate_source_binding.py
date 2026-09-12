"""Real PostgreSQL tenant selection and real Git source binding; no build/reader attestation."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from accessforge_build_worker.coordinator import execute_claim, prepare_and_claim
from accessforge_build_worker.sandbox import DockerSandbox, SandboxPolicy, SandboxRefused
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import read_persisted_source
from accessforge_domain.origins import normalize_origin
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import FindingStatus, Outcome
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    patches,
    projects,
    reviews,
    runs,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence.source_intake import SourceIdentity

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class BoundFixture:
    database: str
    workspace: str
    project: str
    snapshot: str
    repository: Path
    source: SourceSnapshot
    owner: str


@pytest.fixture()
def binding(test_database_url: str, tmp_path: Path) -> Iterator[BoundFixture]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    executable = shutil.which("git")
    assert executable is not None
    source = SourceSnapshot(
        (
            SourceFile("app.py", b"print('owned')\n"),
            SourceFile(
                "build.js",
                b"const fs = require('fs'); "
                b"fs.writeFileSync('/work/out/candidate.txt', fs.readFileSync('app.py'));",
            ),
        )
    )
    for file in source.files:
        (tmp_path / file.path).write_bytes(file.content)

    def git(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - owned synthetic repository setup only.
            [executable, "-c", "core.hooksPath=/dev/null", "-C", str(tmp_path), *args],
            capture_output=True,
            check=True,
            timeout=10,
            env={"PATH": os.defpath, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        )
        return result.stdout.decode().strip()

    git("init", "--object-format=sha1")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-m", "owned source")
    identity = SourceIdentity(git("rev-parse", "HEAD"), source.tree_digest, False)
    workspace, user = str(uuid.uuid4()), str(uuid.uuid4())
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'source-binding')", (workspace,))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
        )
    with workspace_connection(test_database_url, workspace) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (workspace, user),
        )
        project = projects.create_project(
            conn,
            workspace_id=workspace,
            name="owned source",
            repository_url="https://example.test/owned.git",
            repository_authorized_by=user,
        )
        snapshot = projects.record_source_snapshot(
            conn,
            workspace_id=workspace,
            project_id=project,
            identity=identity,
            requested_revision="HEAD",
        )
    yield BoundFixture(test_database_url, workspace, project, snapshot, tmp_path, source, user)
    with workspace_connection(test_database_url, workspace) as conn:
        # Exact synthetic test workspace, in FK dependency order. Other tests/data are untouched.
        conn.execute("DELETE FROM candidate_build_attempt WHERE workspace_id = %s", (workspace,))
        conn.execute("DELETE FROM patch_verification WHERE workspace_id = %s", (workspace,))
        conn.execute("DELETE FROM patch_proposal WHERE workspace_id = %s", (workspace,))
    with unscoped_connection(test_database_url) as conn:
        conn.execute("DELETE FROM workspace WHERE id = %s", (workspace,))
        conn.execute("DELETE FROM app_user WHERE id = %s", (user,))


def test_identity_is_loaded_from_the_persisted_workspace_record(binding: BoundFixture) -> None:
    (binding.repository / "app.py").write_text("mutable checkout is not the source record")
    with workspace_connection(binding.database, binding.workspace) as conn:
        result = read_persisted_source(
            conn,
            workspace_id=binding.workspace,
            source_snapshot_id=binding.snapshot,
            repositories={binding.project: binding.repository},
        )
    assert result.source_snapshot_id == binding.snapshot
    assert result.project_id == binding.project
    assert result.workspace_id == binding.workspace
    assert result.bound.archive_digest == binding.source.archive_digest


def test_foreign_workspace_record_is_not_visible_even_with_its_exact_id(
    binding: BoundFixture,
) -> None:
    foreign = str(uuid.uuid4())
    with workspace_connection(binding.database, foreign) as conn:
        with pytest.raises(SnapshotRefused, match="unavailable"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


def test_repository_path_cannot_be_selected_without_operator_mapping(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        with pytest.raises(SnapshotRefused, match="operator-provisioned"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={},
            )


def test_revoked_project_cannot_load_source(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        conn.execute("UPDATE project SET revoked_at = now() WHERE id = %s", (binding.project,))
        with pytest.raises(SnapshotRefused, match="revoked"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


def test_dirty_database_record_is_not_relabelled_as_a_clean_commit(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        conn.execute(
            "UPDATE source_snapshot SET dirty = true, dirty_path_count = 1 WHERE id = %s",
            (binding.snapshot,),
        )
        with pytest.raises(SnapshotRefused, match="dirty source"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


@pytest.mark.sandbox
@pytest.mark.parametrize("exit_failure", [False, True])
def test_real_source_claim_docker_capture_and_durable_receipt(
    binding: BoundFixture,
    exit_failure: bool,
) -> None:
    """Actual build pipeline over owned synthetic source, not reference-app or reader proof."""
    image = os.environ.get("ACCESSFORGE_SANDBOX_IMAGE")
    if not image:
        pytest.skip("real Docker toolchain not provisioned")
    with workspace_connection(binding.database, binding.workspace) as conn:
        expiry = to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1))
        env = projects.register_environment(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            spec=projects.EnvironmentSpec(
                name="synthetic baseline",
                allowed_origins=frozenset({normalize_origin("http://localhost:8000")}),
                fixture_reset_strategy="RESET_ENDPOINT",
                observer_credential_ref="observer",
                reset_credential_ref="reset",
                permitted_effects=frozenset(),
                expires_at=expiry,
            ),
            authorized_by=binding.owner,
        )
        artifact = projects.record_build_artifact(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            source_snapshot_id=binding.snapshot,
            artifact_digest="a" * 64,
            identity_observable=True,
        )
        seal = projects.seal_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            source_snapshot_id=binding.snapshot,
            build_artifact_id=artifact,
            environment_manifest_id=env,
            inputs=projects.SealInputs(
                journey_digest="a" * 64,
                assertion_set_digest="a" * 64,
                fixture_digest="a" * 64,
                runner_profile_digest="a" * 64,
                navigator_policy_digest="a" * 64,
                evaluator_version="synthetic-test",
                model_config_digest="b" * 64,
            ),
        )
        run = runs.create_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            manifest_digest=seal.manifest_digest,
        )
        finding = reviews.create_finding(
            conn,
            workspace_id=binding.workspace,
            run_id=run,
            assertion_id="synthetic",
            summary="synthetic failure for build dispatch only",
            status=FindingStatus.REPRODUCED,
            run_outcome=Outcome.FAIL,
            actor_id=binding.owner,
        )
        patches.configure_repair_surface(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            paths=("app.py",),
            configured_by=binding.owner,
        )
        proposal = patches.propose_patch(
            conn,
            workspace_id=binding.workspace,
            finding_id=finding,
            base_manifest_digest=seal.manifest_digest,
            base_source_digest=binding.source.tree_digest,
            changes=(ProposedChange("app.py", "print('repaired')\n"),),
            rationale="exercise an owned candidate build",
            proposed_by=binding.owner,
        )
        patches.approve_patch(
            conn,
            workspace_id=binding.workspace,
            patch_id=proposal.patch_id,
            actor_id=binding.owner,
            expires_at=expiry,
        )
    sandbox = DockerSandbox(SandboxPolicy(image=image, wall_seconds=30))
    command: tuple[str, ...] = ("/usr/local/bin/node", "build.js")
    if exit_failure:
        command = ("/usr/local/bin/node", "-e", "console.log('PASS'); process.exit(23)")
    claimed = prepare_and_claim(
        binding.database,
        workspace_id=binding.workspace,
        patch_id=proposal.patch_id,
        repositories={binding.project: binding.repository},
        sandbox=sandbox,
        command=command,
    )
    if exit_failure:
        with pytest.raises(SandboxRefused, match="code 23"):
            execute_claim(
                binding.database,
                workspace_id=binding.workspace,
                claimed=claimed,
                sandbox=sandbox,
                command=command,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            receipt = conn.execute(
                "SELECT state, cleanup_confirmed, artifact_digest "
                "FROM candidate_build_attempt WHERE id = %s",
                (claimed.claim.build_id,),
            ).fetchone()
            assert receipt == {
                "state": "FAILED",
                "cleanup_confirmed": True,
                "artifact_digest": None,
            }
        return
    result = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
    )
    assert result.task_id == claimed.claim.build_id
    assert result.artifact.files == (SourceFile("out/candidate.txt", b"print('repaired')\n"),)
    assert (binding.repository / "app.py").read_bytes() == b"print('owned')\n"
    with workspace_connection(binding.database, binding.workspace) as conn:
        receipt = conn.execute(
            "SELECT state, artifact_digest, cleanup_confirmed "
            "FROM candidate_build_attempt WHERE id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert receipt == {
            "state": "BUILT",
            "artifact_digest": result.artifact.archive_digest,
            "cleanup_confirmed": True,
        }
