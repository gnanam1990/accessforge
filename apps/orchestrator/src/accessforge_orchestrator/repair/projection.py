"""Read-only trusted repair input: retained diagnosis/evidence plus broker-verified Git objects."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from accessforge_build_worker.source_broker import read_persisted_source
from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_orchestrator.diagnosis.models import DiagnosisValidation, SourceIdentity
from accessforge_orchestrator.execution_artifacts import ExecutionArtifactStore, Refused
from accessforge_orchestrator.finalize_execution import _retained
from accessforge_persistence import evaluations

from .worker import OriginalFile, RepairInput


@dataclass(frozen=True, slots=True)
class PreparedRepairInput:
    inputs: RepairInput
    requested_by: str
    run_id: str
    project_id: str
    source_snapshot_id: str
    evaluation_digest: str
    source_archive_digest: str
    surface_revision: int

    @property
    def binding_digest(self) -> str:
        """Compare this again after any model work; not a current authorization token."""
        return digest(
            {
                "inputs": self.inputs.model_dump(mode="json"),
                "requestedBy": self.requested_by,
                "runId": self.run_id,
                "projectId": self.project_id,
                "sourceSnapshotId": self.source_snapshot_id,
                "evaluationDigest": self.evaluation_digest,
                "sourceArchiveDigest": self.source_archive_digest,
                "surfaceRevision": self.surface_revision,
            }
        )


def prepare(
    conn: psycopg.Connection[Any],
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    finding_id: str,
    diagnosis_id: str,
    requested_by: str,
    repositories: Mapping[str, Path],
    cancelled: Callable[[], bool] = lambda: False,
) -> PreparedRepairInput:
    """Caller owns the workspace transaction; do not keep it open across a model invocation.

    Repository mappings are private operator configuration, never browser input. No file is
    written, no model/approval/proposal is created, and no build, hook or reader is started.
    """
    if cancelled() or any(
        str(UUID(value)) != value
        for value in (workspace_id, finding_id, diagnosis_id, requested_by)
    ):
        raise Refused("cancelled or noncanonical repair identity")
    member = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
        (workspace_id, requested_by),
    ).fetchone()
    if member is None or not {
        Permission.PROJECT_CONFIGURE,
        Permission.EVIDENCE_READ,
    } <= permissions_for(Role(member["role"])):
        raise Refused("current repair preparation permission unavailable")
    baseline = conn.execute(
        """
        SELECT f.run_id, f.assertion_id, r.manifest_digest, r.lease_epoch, r.outcome,
               m.canonical_manifest, m.project_id, m.source_snapshot_id,
               s.commit_sha, s.tree_digest, surface.paths, surface.revision AS surface_revision
          FROM finding f
          JOIN run r ON r.id=f.run_id AND r.workspace_id=f.workspace_id
          JOIN sealed_manifest m ON m.run_id=r.id AND m.manifest_digest=r.manifest_digest
               AND m.workspace_id=r.workspace_id AND m.project_id=r.project_id
          JOIN source_snapshot s ON s.id=m.source_snapshot_id AND s.workspace_id=m.workspace_id
               AND s.project_id=m.project_id
          JOIN project p ON p.id=m.project_id AND p.workspace_id=m.workspace_id
          JOIN project_repair_surface surface ON surface.project_id=p.id
               AND surface.workspace_id=p.workspace_id
         WHERE f.id=%s AND f.workspace_id=%s AND f.status IN ('CANDIDATE','REPRODUCED')
               AND r.status='COMPLETED' AND r.outcome IN ('FAIL','INCONCLUSIVE')
               AND p.revoked_at IS NULL AND p.repository_authorized_by IS NOT NULL
         FOR SHARE OF f,r,s,p,surface
        """,
        (finding_id, workspace_id),
    ).fetchone()
    if baseline is None or digest(baseline["canonical_manifest"]) != baseline["manifest_digest"]:
        raise Refused("current authorized original repair baseline unavailable")
    run_id = str(baseline["run_id"])
    original = evaluations.read(conn, run_id=run_id)
    ticket = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE run_id=%s AND workspace_id=%s FOR SHARE",
        (run_id, workspace_id),
    ).fetchone()
    if (
        original is None
        or ticket is None
        or ticket["accepted_at"] is None
        or original["snapshot"]["outcome"] != baseline["outcome"]
        or original["snapshot"]["manifestDigest"] != baseline["manifest_digest"]
        or original["snapshot"]["attemptId"] != str(ticket["attempt_id"])
        or ticket["epoch"] != baseline["lease_epoch"]
    ):
        raise Refused("repair requires its exact original completed evaluation and attempt")
    # Artifacts lock before the diagnosis: evidence retirement takes that same order.
    _, artifact_ids = _retained(
        conn, store, {**ticket, "manifest_digest": baseline["manifest_digest"]}
    )
    if artifact_ids != original["snapshot"]["artifacts"] or not any(
        item["assertionId"] == baseline["assertion_id"] and item["condition"] == "FALSE"
        for item in original["snapshot"]["assertions"]
    ):
        raise Refused("repair lacks its original retained evidence and failed assertion")
    record = conn.execute(
        "SELECT d.* FROM finding_diagnosis d WHERE d.id=%s AND d.finding_id=%s "
        "AND d.workspace_id=%s AND d.run_id=%s AND d.deleted_at IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM finding_diagnosis successor WHERE successor.supersedes=d.id) "
        "FOR SHARE OF d",
        (diagnosis_id, finding_id, workspace_id, run_id),
    ).fetchone()
    if (
        record is None
        or record["payload"] is None
        or digest(record["payload"]) != record["payload_digest"]
        or record["evaluation_digest"] != original["snapshotDigest"]
        or str(record["attempt_id"]) != str(ticket["attempt_id"])
    ):
        raise Refused("exact current retained diagnosis unavailable")
    diagnosis = DiagnosisValidation.model_validate(record["payload"])
    brief = diagnosis.repair_brief
    if (
        diagnosis.support != "SOURCE_LINKED"
        or brief is None
        or brief.stop_recommendation is not None
    ):
        raise Refused("repair requires a supported diagnosis without a stop recommendation")
    source = read_persisted_source(
        conn,
        workspace_id=workspace_id,
        source_snapshot_id=str(baseline["source_snapshot_id"]),
        repositories=repositories,
        cancelled=cancelled,
    )
    if (
        source.bound.commit_sha != baseline["commit_sha"]
        or source.bound.source.tree_digest != baseline["tree_digest"]
        or source.project_id != str(baseline["project_id"])
        or source.bound.commit_sha != baseline["canonical_manifest"].get("sourceCommitSha")
        or source.bound.source.tree_digest != baseline["canonical_manifest"].get("sourceTreeDigest")
    ):
        raise Refused("source broker identity differs from the original finding baseline")
    available = {file.path: file for file in source.bound.source.files}
    files: list[OriginalFile] = []
    for path in brief.allowed_files:
        old = available.get(path)
        if old is None or old.mode not in {0o644, 0o755}:
            raise Refused("complete regular original repair file unavailable")
        files.append(
            OriginalFile(
                path=path,
                text=old.content.decode("utf-8", errors="strict"),
                sha256=hashlib.sha256(old.content).hexdigest(),
                mode="100644" if old.mode == 0o644 else "100755",
            )
        )
    inputs = RepairInput(
        workspace_id=workspace_id,
        finding_id=finding_id,
        diagnosis_id=diagnosis_id,
        diagnosis_digest=digest(diagnosis.model_dump(mode="json")),
        base_manifest_digest=baseline["manifest_digest"],
        source=SourceIdentity(
            commit_sha=source.bound.commit_sha, tree_digest=source.bound.source.tree_digest
        ),
        diagnosis=diagnosis,
        application_paths=tuple(baseline["paths"]),
        files=tuple(files),
    )
    if cancelled():
        raise Refused("repair source preparation cancelled")
    return PreparedRepairInput(
        inputs,
        requested_by,
        run_id,
        str(baseline["project_id"]),
        source.source_snapshot_id,
        original["snapshotDigest"],
        source.bound.archive_digest,
        int(baseline["surface_revision"]),
    )
