"""Protected baseline HTTP/database execution against original retained build bytes."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from accessforge_persistence import (
    baseline_effect_delivery,
    baseline_observations,
    baseline_runs,
    workspace_connection,
)
from accessforge_persistence import baseline_endpoints as endpoints
from accessforge_persistence import baseline_regressions as regressions
from accessforge_persistence.candidate_regressions import RegressionClaim

from .artifacts import CandidateArchiveStore
from .baseline_artifacts import read_retained_baseline
from .candidate_gateway import CandidateGateway
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
    on_baseline_session: Callable[[CandidateGateway], None] | None = None,
    endpoint_origin: str | None = None,
    endpoint_fixture_nonce: str | None = None,
    reserve_fixture: Callable[[RegressionClaim, str], str] | None = None,
    confirm_fixture: Callable[[RegressionClaim, str, dict[str, Any]], None] | None = None,
) -> ReferenceRegressionResult:
    """One protected attempt; explicit session mode binds the original loopback endpoint.

    Process plans commit before Docker create. Observed creation commits before a fresh authority
    check permits activation. Removal facts may arrive after fencing; they never turn UNKNOWN
    into PASSED. Successful measurements are not a screen-reader run outcome.
    Reader startup belongs to the explicit trusted callback, never this worker by default.
    """
    configured = (endpoint_origin, endpoint_fixture_nonce, reserve_fixture, confirm_fixture)
    if (on_baseline_session is None and any(v is not None for v in configured)) or (
        on_baseline_session is not None and any(v is None for v in configured)
    ):
        raise regressions.Refused(
            "baseline session requires original origin, nonce and fixture callbacks"
        )
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
            endpoint_required=on_baseline_session is not None,
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

    def endpoint_authority() -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.assert_active(conn, claim=claim)

    def endpoint_live() -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            endpoints.assert_live(conn, claim=claim)

    def candidate_request(method: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            row = conn.execute(
                "SELECT run_id FROM baseline_regression_attempt WHERE id=%s", (claim.attempt_id,)
            ).fetchone()
            assert row is not None
            baseline_runs.assert_request(conn, run_id=str(row["run_id"]), method=method)

    def begin_effect(path: str, body: str) -> dict[str, Any] | None:
        with workspace_connection(database_url, workspace_id) as conn:
            permission = baseline_effect_delivery.begin(
                conn,
                claim=claim,
                path=path,
                body=body,
            )
        return permission  # Commit (including consumption) MUST finish before HTTP can be sent.

    def check_effect(permission: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            baseline_effect_delivery.check(conn, claim=claim, permission=permission)

    def effect_response(permission: dict[str, Any], response: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            baseline_effect_delivery.retain_response(
                conn, claim=claim, permission=permission, response=response
            )

    def endpoint_planned(identity: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            endpoints.plan(conn, claim=claim, identity=identity)

    def endpoint_bound(receipt: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            endpoints.bound(conn, claim=claim, receipt=receipt)

    def endpoint_closed(clean: bool) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            endpoints.closed(conn, claim=claim, cleanup_confirmed=clean)

    def artifact_observed(observation: dict[str, Any]) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            baseline_observations.retain(conn, claim=claim, observation=observation)

    def session(gateway: CandidateGateway) -> None:
        gateway.receipt()
        with workspace_connection(database_url, workspace_id) as conn:
            baseline_runs.prepare(conn, claim=claim)
        assert on_baseline_session is not None
        try:
            on_baseline_session(gateway)
        finally:
            try:
                with workspace_connection(database_url, workspace_id) as conn:
                    baseline_runs.assert_reader_released(conn, attempt_id=claim.attempt_id)
            except Exception as exc:
                raise CleanupUnconfirmed("baseline reader stop could not be confirmed") from exc

    session_kwargs: dict[str, Any] = {}
    if on_baseline_session is not None:
        assert reserve_fixture is not None and confirm_fixture is not None
        session_kwargs = dict(
            on_candidate_endpoint=session,
            endpoint_origin=endpoint_origin,
            endpoint_fixture_nonce=endpoint_fixture_nonce,
            assert_endpoint_authority=endpoint_authority,
            assert_endpoint_live=endpoint_live,
            assert_candidate_request=candidate_request,
            begin_candidate_effect=begin_effect,
            assert_candidate_effect=check_effect,
            on_candidate_effect_response=effect_response,
            on_endpoint_planned=endpoint_planned,
            on_endpoint_bound=endpoint_bound,
            on_endpoint_closed=endpoint_closed,
            on_artifact_observed=artifact_observed,
            reserve_candidate_fixture=lambda nonce: reserve_fixture(claim, nonce),
            confirm_candidate_fixture=lambda fingerprint, application: confirm_fixture(
                claim, fingerprint, application
            ),
        )
    try:
        result = runner.run(
            artifact,
            task_id=claim.attempt_id,
            cancelled=cancelled,
            on_planned=planned,
            on_created=created,
            on_removed=removed,
            **session_kwargs,
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
