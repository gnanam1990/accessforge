"""Read-only baseline source preparation; no Docker dispatch, artifact receipt or reader proof."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_persistence import execution_approvals, workspace_connection

from .snapshot import SnapshotRefused, SourceSnapshot
from .source_broker import read_persisted_source


@dataclass(frozen=True, slots=True)
class BaselineBinding:
    workspace_id: str
    run_id: str
    project_id: str
    manifest_digest: str
    source_snapshot_id: str
    source_tree_digest: str
    expected_artifact_digest: str


@dataclass(frozen=True, slots=True)
class PreparedBaselineSource:
    binding: BaselineBinding
    source: SourceSnapshot


def _binding(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, run_id: str
) -> BaselineBinding:
    run = conn.execute("SELECT * FROM run WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
    if (
        run is None
        or str(run["workspace_id"]) != workspace_id
        or run["status"] != "QUEUED"
        or run["cancel_requested_at"] is not None
        or run["quarantined"]
        or conn.execute("SELECT 1 FROM desktop_lease WHERE run_id=%s", (run_id,)).fetchone()
        or conn.execute("SELECT 1 FROM candidate_run_binding WHERE run_id=%s", (run_id,)).fetchone()
    ):
        raise SnapshotRefused("baseline preparation requires an unleased approved queued baseline")
    seal = conn.execute(
        "SELECT id,project_id,source_snapshot_id FROM sealed_manifest WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if seal is None:
        raise SnapshotRefused("original baseline seal is unavailable")
    conn.execute("SELECT id FROM approval WHERE id=%s FOR SHARE", (run["authorization_id"],))
    manifest = execution_approvals.assert_authorized(
        conn, sealed_manifest_id=str(seal["id"]), run_id=run_id, workspace_id=workspace_id
    )
    if (
        digest(manifest) != run["manifest_digest"]
        or manifest["authorizationId"] != str(run["authorization_id"])
        or str(seal["project_id"]) != str(run["project_id"])
    ):
        raise SnapshotRefused("baseline run and its original authority/manifest differ")
    return BaselineBinding(
        workspace_id,
        run_id,
        str(seal["project_id"]),
        run["manifest_digest"],
        str(seal["source_snapshot_id"]),
        manifest["sourceTreeDigest"],
        manifest["buildArtifactDigest"],
    )


def prepare_baseline_source(
    database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    repositories: Mapping[str, Path],
    cancelled: Callable[[], bool] = lambda: False,
) -> PreparedBaselineSource:
    """Recover original Git objects, then recheck authority after the potentially slow read.

    Repository paths come only from operator configuration. Each context closes before the next;
    approval locks must not prevent a revocation from taking effect while Git is being read.
    Returned inputs confer no execution authority: a durable worker claim and a fresh dispatch
    check are still required before building or starting the baseline application.
    """
    if cancelled():
        raise SnapshotRefused("baseline source preparation cancelled")
    with workspace_connection(database_url, workspace_id) as conn:
        original = _binding(conn, workspace_id=workspace_id, run_id=run_id)
    with workspace_connection(database_url, workspace_id) as conn:
        recovered = read_persisted_source(
            conn,
            workspace_id=workspace_id,
            source_snapshot_id=original.source_snapshot_id,
            repositories=repositories,
            cancelled=cancelled,
        )
    if (
        recovered.workspace_id != workspace_id
        or recovered.project_id != original.project_id
        or recovered.source_snapshot_id != original.source_snapshot_id
        or recovered.bound.source.tree_digest != original.source_tree_digest
    ):
        raise SnapshotRefused("recovered source differs from the exact sealed baseline")
    with workspace_connection(database_url, workspace_id) as conn:
        current = _binding(conn, workspace_id=workspace_id, run_id=run_id)
    if current != original or cancelled():
        raise SnapshotRefused("baseline authority changed or source preparation was cancelled")
    return PreparedBaselineSource(original, recovered.bound.source)
