"""Compose an explicitly authorized baseline runtime with post-closure evidence completion.

The operator still supplies the trusted reader controller and its original private journal.
Importing this module starts nothing. Do not retry this entrypoint after an uncertain response;
use complete_execution.complete to reconcile evidence without replaying the desktop session.
"""

from collections.abc import Callable
from typing import Any

from accessforge_build_worker.artifacts import CandidateArchiveStore
from accessforge_build_worker.baseline_session import BaselineSession
from accessforge_build_worker.reference_regressions import ReferenceRegressions
from accessforge_persistence import baseline_builds

from .baseline_session_runtime import execute_baseline_session
from .complete_execution import complete
from .execution_artifacts import ExecutionArtifactStore


def execute_and_complete(
    database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    build_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    runner: ReferenceRegressions,
    archive_store: CandidateArchiveStore,
    evidence_store: ExecutionArtifactStore,
    journal_path: str,
    on_session: Callable[[BaselineSession], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Execute once, then retain/finalize only after protected runtime closure commits.

    Reader callback return alone is insufficient: execute_baseline_session must finish endpoint,
    original-epoch STOP, process cleanup and functional-receipt persistence first. Completion
    independently validates the original journal/session/retained bytes; it never accepts a
    callback-provided verdict. This API does not supply startup consent or qualify a reader.
    """
    if not callable(on_session) or not isinstance(journal_path, str) or not journal_path:
        raise baseline_builds.Refused("baseline requires its reader controller and journal path")
    entered = False

    def reader(session: BaselineSession) -> None:
        nonlocal entered
        if entered:
            raise baseline_builds.Refused("baseline reader callback cannot be replayed")
        entered = True
        on_session(session)

    execute_baseline_session(
        database_url,
        workspace_id=workspace_id,
        run_id=run_id,
        build_id=build_id,
        origin=origin,
        reset_credential_ref=reset_credential_ref,
        observer_credential_ref=observer_credential_ref,
        runner=runner,
        store=archive_store,
        on_session=reader,
        cancelled=cancelled,
    )
    if not entered:
        raise baseline_builds.Refused("baseline reader session was not entered")
    # Do not suppress retention just because execution cancellation arrived after closure:
    # retaining the already-stopped original evidence is not another desktop action.
    return complete(
        database_url,
        evidence_store,
        workspace_id=workspace_id,
        run_id=run_id,
        journal_path=journal_path,
    )
