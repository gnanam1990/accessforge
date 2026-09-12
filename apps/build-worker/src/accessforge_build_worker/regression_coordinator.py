"""Commit a protected-regression claim before Docker, then publish only fenced cleanup receipts."""

from __future__ import annotations

from collections.abc import Callable

from accessforge_persistence import candidate_regressions as regressions
from accessforge_persistence import workspace_connection

from .artifacts import CandidateArchiveStore, read_retained_candidate
from .process import CommandStopped
from .reference_regressions import ReferenceRegressionResult, ReferenceRegressions
from .sandbox import CleanupUnconfirmed


def execute_regressions(
    database_url: str,
    *,
    workspace_id: str,
    build_id: str,
    runner: ReferenceRegressions,
    store: CandidateArchiveStore,
    cancelled: Callable[[], bool] = lambda: False,
) -> ReferenceRegressionResult:
    """One trusted attempt per immutable retained build; never resume an ambiguous execution.

    PASSED is a build-linked protected test receipt, not an actual candidate-run attestation or
    VERIFIED repair. The future matched-reader dispatcher must bind its run to this exact build.
    """
    artifact = read_retained_candidate(
        database_url,
        workspace_id=workspace_id,
        build_id=build_id,
        store=store,
    )
    policy = runner.policy_digest()
    with workspace_connection(database_url, workspace_id) as conn:
        claim = regressions.claim(
            conn,
            workspace_id=workspace_id,
            build_id=build_id,
            artifact_digest=artifact.archive_digest,
            policy_digest=policy,
            image_id=runner.image,
            daemon_endpoint=runner.sandbox.daemon.endpoint,
            daemon_id=runner.sandbox.daemon.daemon_id,
        )
    with workspace_connection(database_url, workspace_id) as conn:
        regressions.dispatch(
            conn,
            claim=claim,
            policy_digest=runner.policy_digest(),
            artifact_digest=artifact.archive_digest,
        )

    def planned(role: str, name: str, image: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.planned(conn, claim=claim, role=role, name=name, image_id=image)

    def created(role: str, container: str, image: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.created(
                conn, claim=claim, role=role, container_id=container, image_id=image
            )
        # Keep observed identity even when a concurrent fence now denies process activation.
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.assert_active(conn, claim=claim)

    def removed(role: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.removed(conn, claim=claim, role=role)

    try:
        result = runner.run(
            artifact,
            task_id=claim.attempt_id,
            cancelled=cancelled,
            on_planned=planned,
            on_created=created,
            on_removed=removed,
        )
        if result.task_id != claim.attempt_id or result.daemon != runner.sandbox.daemon:
            raise regressions.Refused("regression task or daemon identity changed")
        # Reverify retained bytes/storage/retention before publication; retirement or restore
        # cannot silently leave this worker reporting unavailable/substituted candidate bytes.
        current = read_retained_candidate(
            database_url,
            workspace_id=workspace_id,
            build_id=build_id,
            store=store,
        )
        if current.archive_digest != artifact.archive_digest:
            raise regressions.Refused("retained candidate changed during regressions")
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.finish(
                conn,
                claim=claim,
                policy_digest=runner.policy_digest(),
                artifact_digest=result.artifact_digest,
                checks=result.checks,
                containers=result.containers,
            )
        return result
    except Exception as exc:
        failure_code = "REGRESSION_FAILED"
        if isinstance(exc, CommandStopped):
            failure_code = (
                "CANCELLED_CONFIRMED" if str(exc) == "cancelled" else "EXECUTION_INTERRUPTED"
            )
        with workspace_connection(database_url, workspace_id) as conn:
            try:
                regressions.fail(
                    conn,
                    claim=claim,
                    cleanup_confirmed=not isinstance(exc, CleanupUnconfirmed),
                    failure_code=failure_code,
                )
            except regressions.Refused:
                regressions.fence_expired(conn)
        raise
