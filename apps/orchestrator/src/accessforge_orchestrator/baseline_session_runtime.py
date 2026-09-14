"""Compose the approved baseline fixture, owned endpoint and trusted reader callback.

Calling this starts the protected runtime. Merely importing it starts nothing. The operator
must supply the actual reader integration; callback completion is never a reader verdict.
"""

from collections.abc import Callable
from typing import Any

from accessforge_build_worker.artifacts import CandidateArchiveStore
from accessforge_build_worker.baseline_regression_coordinator import execute_baseline_regressions
from accessforge_build_worker.candidate_gateway import CandidateGateway
from accessforge_build_worker.reference_regressions import (
    ReferenceRegressionResult,
    ReferenceRegressions,
)
from accessforge_persistence import baseline_builds, workspace_connection
from accessforge_persistence.candidate_regressions import RegressionClaim

from . import baseline_fixture_runtime as fixtures


def execute_baseline_session(
    database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    build_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    runner: ReferenceRegressions,
    store: CandidateArchiveStore,
    on_session: Callable[[CandidateGateway], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> ReferenceRegressionResult:
    if cancelled():
        raise baseline_builds.Refused("baseline session cancelled before fixture preparation")
    with workspace_connection(database_url, workspace_id) as conn:
        build = conn.execute(
            "SELECT run_id FROM baseline_build_attempt WHERE id=%s", (build_id,)
        ).fetchone()
        if build is None or str(build["run_id"]) != run_id:
            raise baseline_builds.Refused("baseline session requires its original run/build")
        baseline_builds.read_binding(conn, workspace_id=workspace_id, run_id=run_id)
        context = fixtures.prepare_context(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            origin=origin,
            reset_credential_ref=reset_credential_ref,
            observer_credential_ref=observer_credential_ref,
            reset_values={"variant": "inaccessible"},
            observer_config={"effect": "CREATE_TEST_REQUEST"},
        )

    def reserve(claim: RegressionClaim, nonce: str) -> str:
        with workspace_connection(database_url, workspace_id) as conn:
            fingerprint = fixtures.reserve(conn, claim=claim, context=context, nonce=nonce)
        return fingerprint  # Commit must complete before the protected fixture POST.

    def confirm(claim: RegressionClaim, fingerprint: str, application: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            fixtures.confirm(
                conn,
                claim=claim,
                context=context,
                context_digest=fingerprint,
                application=application,
            )

    return execute_baseline_regressions(
        database_url,
        workspace_id=workspace_id,
        build_id=build_id,
        runner=runner,
        store=store,
        cancelled=cancelled,
        on_baseline_session=on_session,
        endpoint_origin=context["origin"],
        endpoint_fixture_nonce=context["nonce"],
        reserve_fixture=reserve,
        confirm_fixture=confirm,
    )
