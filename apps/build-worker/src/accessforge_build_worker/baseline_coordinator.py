"""Owned baseline build dispatch. Captured output is transient until separately retained."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from accessforge_persistence import baseline_builds as builds
from accessforge_persistence import workspace_connection

from .baseline_source import PreparedBaselineSource
from .coordinator import execution_policy
from .sandbox import CleanupUnconfirmed, DockerSandbox, SandboxBuild, SandboxCreation


def execute_baseline_build(
    database_url: str,
    *,
    prepared: PreparedBaselineSource,
    sandbox: DockerSandbox,
    command: tuple[str, ...],
    cancelled: Callable[[], bool] = lambda: False,
) -> SandboxBuild:
    """Commit intent before creation; confirm exact capture and cleanup without claiming retention.

    No automatic retry: a lost returned output needs reconciliation/new authorized run, not a
    second container under an old task. No patch application, baseline app or reader is started.
    """
    if cancelled():
        raise builds.Refused("baseline build cancelled before reservation")
    if sandbox.policy.wall_seconds > 120:
        raise builds.Refused("baseline build must reserve capture time within its ownership lease")
    # Resolve a pinned image reference read-only before reserving an executable task. The image
    # content ID, not its distribution manifest digest, must match the creation/capture receipts.
    image_id = (
        sandbox._checked(
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            sandbox.policy.image,
            deadline=time.monotonic() + 5,
        )
        .stdout.decode()
        .strip()
    )
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise builds.Refused("baseline toolchain image identity unavailable")
    inputs: dict[str, Any] = dict(
        binding=asdict(prepared.binding),
        source_archive_digest=prepared.source.archive_digest,
        policy_digest=execution_policy(sandbox, command),
        image_id=image_id,
        daemon_endpoint=sandbox.daemon.endpoint,
        daemon_id=sandbox.daemon.daemon_id,
    )
    if prepared.source.tree_digest != prepared.binding.source_tree_digest:
        raise builds.Refused("prepared baseline source changed")
    workspace = prepared.binding.workspace_id
    with workspace_connection(database_url, workspace) as conn:
        value = builds.claim(conn, **inputs)
    with workspace_connection(database_url, workspace) as conn:
        builds.dispatch(conn, value=value, **inputs)

    def created(process: SandboxCreation) -> None:
        if process.task_id != value.attempt_id or cancelled():
            raise builds.Refused("baseline creation identity changed or execution cancelled")
        with workspace_connection(database_url, workspace) as conn:
            builds.created(
                conn,
                value=value,
                container_id=process.container_id,
                platform=process.platform,
                image_id=process.image_id,
                daemon_endpoint=process.daemon.endpoint,
                daemon_id=process.daemon.daemon_id,
            )

    try:
        result = sandbox.build(
            prepared.source,
            command=command,
            task_id=value.attempt_id,
            on_created=created,
            cancelled=cancelled,
        )
        if (
            result.task_id != value.attempt_id
            or result.cleanup_confirmed is not True
            or cancelled()
        ):
            raise CleanupUnconfirmed("baseline capture/cleanup could not be confirmed")
        with workspace_connection(database_url, workspace) as conn:
            builds.captured(
                conn,
                value=value,
                container_id=result.container_id,
                platform=result.platform,
                source_archive_digest=result.source_archive_digest,
                artifact_digest=result.artifact.archive_digest,
                policy_digest=execution_policy(sandbox, command),
                image_id=result.image_id,
                daemon_endpoint=result.daemon.endpoint,
                daemon_id=result.daemon.daemon_id,
            )
        return result
    except Exception as exc:
        with workspace_connection(database_url, workspace) as conn:
            try:
                builds.fail(
                    conn, value=value, cleanup_confirmed=not isinstance(exc, CleanupUnconfirmed)
                )
            except builds.Refused:
                builds.fence_expired(conn)
        raise
