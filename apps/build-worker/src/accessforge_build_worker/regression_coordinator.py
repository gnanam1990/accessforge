"""Commit a protected-regression claim before Docker, then publish only fenced cleanup receipts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_persistence import candidate_endpoints as endpoints
from accessforge_persistence import candidate_observations, candidate_runs, workspace_connection
from accessforge_persistence import candidate_regressions as regressions

from .artifact_probe import ArtifactProbe
from .artifacts import CandidateArchiveStore, read_retained_candidate
from .candidate_gateway import CandidateGateway
from .process import CommandStopped
from .reference_regressions import ReferenceRegressionResult, ReferenceRegressions
from .sandbox import CleanupUnconfirmed


@dataclass(frozen=True, slots=True)
class CandidateSession:
    """Trusted controller capability, never exposed to the navigator or patch author."""

    gateway: CandidateGateway
    prepare_run: Callable[[str], dict[str, Any]]

    def artifact_probe(self, *, private_directory: Path) -> ArtifactProbe:
        """Opt-in private same-host measurement; lifetime must be inside this candidate session."""
        return ArtifactProbe(self.gateway, private_directory=private_directory)


def execute_regressions(
    database_url: str,
    *,
    workspace_id: str,
    build_id: str,
    runner: ReferenceRegressions,
    store: CandidateArchiveStore,
    cancelled: Callable[[], bool] = lambda: False,
    on_candidate_endpoint: Callable[[CandidateGateway], None] | None = None,
    on_candidate_session: Callable[[CandidateSession], None] | None = None,
) -> ReferenceRegressionResult:
    """One trusted attempt per immutable retained build; never resume an ambiguous execution.

    PASSED is a build-linked protected test receipt, not an actual candidate-run attestation or
    VERIFIED repair. The future matched-reader dispatcher must bind its run to this exact build.
    """
    if on_candidate_endpoint is not None and on_candidate_session is not None:
        raise regressions.Refused("choose a preview or a candidate session, never both")
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
            endpoint_required=on_candidate_endpoint is not None or on_candidate_session is not None,
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

    def endpoint_authority() -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            regressions.assert_active(conn, claim=claim)

    def endpoint_live() -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            endpoints.assert_live(conn, claim=claim)

    def candidate_request(method: str) -> None:
        with workspace_connection(database_url, workspace_id) as conn:
            candidate_runs.assert_request(conn, attempt_id=claim.attempt_id, method=method)

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
            candidate_observations.retain(conn, claim=claim, observation=observation)

    def session(gateway: CandidateGateway) -> None:
        def prepare_run(environment_id: str) -> dict[str, Any]:
            gateway.receipt()
            current = read_retained_candidate(
                database_url, workspace_id=workspace_id, build_id=build_id, store=store
            )
            with workspace_connection(database_url, workspace_id) as conn:
                return candidate_runs.prepare(
                    conn,
                    claim=claim,
                    environment_id=environment_id,
                    observed_artifact_digest=current.archive_digest,
                    observed_fixture_digest=REFERENCE_FIXTURE_DIGEST,
                )

        assert on_candidate_session is not None
        try:
            on_candidate_session(CandidateSession(gateway, prepare_run))
        finally:
            # Callback failure is not evidence that a previously admitted reader stopped.
            try:
                with workspace_connection(database_url, workspace_id) as conn:
                    candidate_runs.assert_reader_released(conn, attempt_id=claim.attempt_id)
            except Exception as exc:
                raise CleanupUnconfirmed("candidate reader cleanup could not be confirmed") from exc

    try:
        result = runner.run(
            artifact,
            task_id=claim.attempt_id,
            cancelled=cancelled,
            on_planned=planned,
            on_created=created,
            on_removed=removed,
            on_candidate_endpoint=session
            if on_candidate_session is not None
            else on_candidate_endpoint,
            assert_endpoint_authority=endpoint_authority,
            assert_endpoint_live=endpoint_live,
            assert_candidate_request=candidate_request,
            on_endpoint_planned=endpoint_planned,
            on_endpoint_bound=endpoint_bound,
            on_endpoint_closed=endpoint_closed,
            on_artifact_observed=artifact_observed,
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
