"""Real PostgreSQL tenant selection and real Git source binding; no build/reader attestation."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import read_persisted_source
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    projects,
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


@pytest.fixture()
def binding(test_database_url: str, tmp_path: Path) -> Iterator[BoundFixture]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    executable = shutil.which("git")
    assert executable is not None
    source = SourceSnapshot((SourceFile("app.py", b"print('owned')\n"),))
    (tmp_path / "app.py").write_bytes(source.files[0].content)

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
    yield BoundFixture(test_database_url, workspace, project, snapshot, tmp_path, source)
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
