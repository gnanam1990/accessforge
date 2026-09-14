"""Protected baseline HTTP/database execution against original retained build bytes."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from accessforge_persistence import baseline_regressions as regressions
from accessforge_persistence import workspace_connection

from .artifacts import CandidateArchiveStore
from .baseline_artifacts import read_retained_baseline
from .reference_regressions import ReferenceRegressionResult, ReferenceRegressions
from .sandbox import CleanupUnconfirmed


def execute_baseline_regressions(
    database_url: str,
    *,
    workspace_id: str,
    build_id: str,
    runner: ReferenceRegressions,
    store: CandidateArchiveStore,
    cancelled: Callable[[], bool] = lambda: False,
) -> ReferenceRegressionResult:
    """One protected attempt, no public endpoint, reader startup or caller-supplied verdict.

    Process plans commit before Docker create. Observed creation commits before a fresh authority
    check permits activation. Removal facts may arrive after fencing; they never turn UNKNOWN
    into PASSED. Successful measurements are not a screen-reader run outcome.
    """
    if cancelled():
        raise regressions.Refused("baseline protected execution cancelled")
    artifact = read_retained_baseline(
        database_url, workspace_id=workspace_id, build_id=build_id, store=store
    )
    image_id = (
        runner.sandbox._checked(
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            runner.image,
            deadline=time.monotonic() + 5,
        )
        .stdout.decode()
        .strip()
    )
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise regressions.Refused("baseline runtime image identity unavailable")
    with workspace_connection(database_url, workspace_id) as conn:
        claim = regressions.claim(
            conn,
            build_id=build_id,
            artifact_digest=artifact.archive_digest,
            policy_digest=runner.policy_digest(),
            image_id=image_id,
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
        if cancelled():
            raise regressions.Refused("baseline runtime cancelled before process reservation")
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.planned(conn, claim=claim, role=role, name=name, image_id=image)

    def created(role: str, container: str, image: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.created(
                conn, claim=claim, role=role, container_id=container, image_id=image
            )
        if cancelled():
            raise regressions.Refused("baseline runtime cancelled before process activation")
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
        if (
            result.task_id != claim.attempt_id
            or result.daemon != runner.sandbox.daemon
            or cancelled()
        ):
            raise regressions.Refused("baseline runtime receipt or cancellation changed")
        current = read_retained_baseline(
            database_url, workspace_id=workspace_id, build_id=build_id, store=store
        )
        if current.archive_digest != artifact.archive_digest:
            raise regressions.Refused("baseline retained bytes changed during execution")
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.finish(
                conn,
                claim=claim,
                policy_digest=runner.policy_digest(),
                artifact_digest=result.artifact_digest,
                checks=result.checks,
                containers=result.containers,
                validation=result.validation,
            )
        return result
    except Exception as exc:
        with workspace_connection(database_url, workspace_id) as conn:
            try:
                regressions.fail(
                    conn, claim=claim, cleanup_confirmed=not isinstance(exc, CleanupUnconfirmed)
                )
            except regressions.Refused:
                regressions.fence_expired(conn)
        raise
