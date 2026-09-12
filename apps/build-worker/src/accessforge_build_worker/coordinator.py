"""Trusted in-process E0 coordinator. No public endpoint and no automatic resume/retry.

Commit durable intent before Docker creation; commit only supervisor-captured results after exact
cleanup. Crash recovery fences expired attempts; UNKNOWN needs operator reconciliation. Artifact
bytes are returned to the trusted caller; durable artifact retention and protected tests are next.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import approvals, patches, workspace_connection
from accessforge_persistence import candidate_builds as builds

from .sandbox import MEMORY, PIDS, SCRATCH, USER, CleanupUnconfirmed, DockerSandbox, SandboxBuild
from .snapshot import PreparedCandidate, SnapshotRefused, prepare_candidate
from .source_broker import read_persisted_source


@dataclass(frozen=True, slots=True)
class ClaimedCandidate:
    claim: builds.BuildClaim
    inputs: builds.BuildInputs
    candidate: PreparedCandidate


def execution_policy(sandbox: DockerSandbox, command: tuple[str, ...]) -> str:
    return digest(
        {
            "version": "e0-docker-v2",
            "daemon": asdict(sandbox.daemon),
            "isolatedCliConfig": True,
            "toolchain": asdict(sandbox.policy),
            "command": list(command),
            "memory": MEMORY,
            "pids": PIDS,
            "scratch": SCRATCH,
            "user": USER,
            "network": "none",
            "ipc": "none",
            "readonly": True,
            "capDrop": ["ALL"],
            "noNewPrivileges": True,
            "cpus": 1,
            "nofile": 128,
            "core": 0,
            "logDriver": "none",
        }
    )


def prepare_and_claim(
    database_url: str,
    *,
    workspace_id: str,
    patch_id: str,
    repositories: Mapping[str, Path],
    sandbox: DockerSandbox,
    command: tuple[str, ...],
    cancelled: Callable[[], bool] = lambda: False,
) -> ClaimedCandidate:
    """Recover the exact baseline, prepare its patch, and claim under fresh authority."""
    with workspace_connection(database_url, workspace_id) as conn:
        patch = patches.load_patch(conn, patch_id=patch_id)
        baseline = builds._baseline(conn, patch_id, workspace_id)
        source = read_persisted_source(
            conn,
            workspace_id=workspace_id,
            source_snapshot_id=str(baseline["source_snapshot_id"]),
            repositories=repositories,
            cancelled=cancelled,
        )
        if patch.approval_id is None:
            raise SnapshotRefused("patch has no recorded approval")
        candidate = prepare_candidate(
            source.bound.source,
            patch=patch,
            approval=approvals.load_for_check(conn, approval_id=patch.approval_id),
            workspace_id=workspace_id,
            application_paths=tuple(baseline["paths"]),
            now=to_rfc3339_utc(datetime.now(UTC)),
        )
        inputs = builds.BuildInputs(
            source.source_snapshot_id,
            source.bound.commit_sha,
            candidate.base_tree_digest,
            candidate.base_archive_digest,
            candidate.source.archive_digest,
            execution_policy(sandbox, command),
            builds.surface_identity(tuple(baseline["paths"]), int(baseline["surface_revision"])),
            patch.patch_digest,
            patch.revision,
            sandbox.daemon.endpoint,
            sandbox.daemon.daemon_id,
        )
    # This transaction commits before this function returns. Preparation did not execute source.
    with workspace_connection(database_url, workspace_id) as conn:
        claim = builds.claim_build(
            conn,
            workspace_id=workspace_id,
            patch_id=patch_id,
            inputs=inputs,
            lease_seconds=math.ceil(sandbox.policy.wall_seconds) + 60,
        )
    return ClaimedCandidate(claim, inputs, candidate)


def execute_claim(
    database_url: str,
    *,
    workspace_id: str,
    claimed: ClaimedCandidate,
    sandbox: DockerSandbox,
    command: tuple[str, ...],
    cancelled: Callable[[], bool] = lambda: False,
) -> SandboxBuild:
    """Dispatch once and fence late publication. Returned artifact bytes are not reader proof."""
    inputs, candidate, claim = claimed.inputs, claimed.candidate, claimed.claim
    if (
        execution_policy(sandbox, command) != inputs.policy_digest
        or candidate.source.archive_digest != inputs.candidate_archive_digest
        or candidate.base_archive_digest != inputs.base_archive_digest
        or candidate.base_tree_digest != inputs.source_tree_digest
        or candidate.patch_digest != inputs.patch_digest
    ):
        raise builds.BuildClaimRefused("prepared source or execution configuration changed")
    with workspace_connection(database_url, workspace_id) as conn:
        builds.authorize_dispatch(conn, workspace_id=workspace_id, claim=claim, inputs=inputs)
    # The attempt ID already exists durably and determines the Docker name even after a crash.
    try:
        result = sandbox.build(
            candidate.source, command=command, task_id=claim.build_id, cancelled=cancelled
        )
    except Exception as exc:
        with workspace_connection(database_url, workspace_id) as conn:
            try:
                builds.record_failure(
                    conn, claim=claim, cleanup_confirmed=not isinstance(exc, CleanupUnconfirmed)
                )
            except builds.BuildClaimRefused:
                builds.fence_expired(conn)
        raise
    with workspace_connection(database_url, workspace_id) as conn:
        if (
            result.daemon.endpoint != inputs.daemon_endpoint
            or result.daemon.daemon_id != inputs.daemon_id
        ):
            raise builds.BuildClaimRefused("build receipt belongs to another daemon")
        builds.finish_build(
            conn,
            claim=claim,
            artifact_digest=result.artifact.archive_digest,
            candidate_archive_digest=result.source_archive_digest,
            cleanup_confirmed=result.cleanup_confirmed,
        )
    return result
