"""Compose an explicitly authorized baseline runtime with post-closure evidence completion.

The operator still supplies the trusted reader controller and its original private journal.
Importing this module starts nothing. Do not retry this entrypoint after an uncertain response;
use complete_execution.complete to reconcile evidence without replaying the desktop session.
"""

import asyncio
import math
from collections.abc import Callable
from typing import Any

from accessforge_build_worker.artifacts import CandidateArchiveStore
from accessforge_build_worker.baseline_session import BaselineSession
from accessforge_build_worker.reference_regressions import ReferenceRegressions
from accessforge_persistence import baseline_builds

from .baseline_reader_dispatch import admit_dispatch_and_wait_reader
from .baseline_session_runtime import execute_baseline_session
from .complete_execution import complete
from .execution_artifacts import ExecutionArtifactStore
from .manual_dispatch import StartTransport, UnavailableTransport


def dispatch_and_complete(
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
    runner_id: str,
    attempt_id: str,
    transport: StartTransport | None = None,
    timeout_seconds: float = 60,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Synchronous operator boundary: provision, dispatch once, await STOP, retain/finalize.

    Supply a qualified trusted transport, not a navigator-authored callback. This entrypoint
    refuses unavailable transport and invalid configuration before provisioning any runtime.
    Call outside an event loop. A failed/uncertain invocation must not be replayed: reconcile
    its original attempt and use complete() after independently confirmed runtime closure.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 60
    ):
        raise ValueError("baseline reader wait must be within (0, 60] seconds")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError("baseline operator must run outside an active event loop")
    if cancelled():
        raise baseline_builds.Refused("baseline cancelled before runtime provisioning")
    selected_transport = transport if transport is not None else UnavailableTransport()
    selected_transport.check_available()

    def reader(session: BaselineSession) -> None:
        if cancelled():
            raise baseline_builds.Refused("baseline cancelled before reader admission")
        asyncio.run(
            admit_dispatch_and_wait_reader(
                session,
                runner_id=runner_id,
                attempt_id=attempt_id,
                transport=selected_transport,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
            )
        )

    return execute_and_complete(
        database_url,
        workspace_id=workspace_id,
        run_id=run_id,
        build_id=build_id,
        origin=origin,
        reset_credential_ref=reset_credential_ref,
        observer_credential_ref=observer_credential_ref,
        runner=runner,
        archive_store=archive_store,
        evidence_store=evidence_store,
        journal_path=journal_path,
        on_session=reader,
        cancelled=cancelled,
    )


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
