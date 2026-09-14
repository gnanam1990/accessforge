"""Private original-run handoff to the trusted native controller, never navigator input."""

from dataclasses import dataclass, field
from pathlib import Path

from accessforge_persistence import baseline_runs, runners, workspace_connection
from accessforge_persistence.candidate_regressions import RegressionClaim

from .artifact_probe import ArtifactProbe
from .candidate_gateway import CandidateGateway


@dataclass(frozen=True, slots=True)
class BaselineSession:
    database_url: str = field(repr=False)
    workspace_id: str
    claim: RegressionClaim = field(repr=False)
    gateway: CandidateGateway = field(repr=False)

    def artifact_probe(self, *, private_directory: Path) -> ArtifactProbe:
        """Native preflight bridge; keep its context inside the baseline session callback."""
        return ArtifactProbe(self.gateway, private_directory=private_directory)

    def admit_reader(self, *, runner_id: str, attempt_id: str) -> runners.AdmittedLease:
        """Admit only within the existing endpoint/approval window; never start or qualify AT.

        The ordinary runner gate still requires the original matching successful preflight.
        The returned lease is not a dispatch ticket; authenticated manual dispatch is next.
        """
        self.gateway.receipt()
        with workspace_connection(self.database_url, self.workspace_id) as conn:
            parent = conn.execute(
                "SELECT run_id FROM baseline_regression_attempt WHERE id=%s AND workspace_id=%s",
                (self.claim.attempt_id, self.workspace_id),
            ).fetchone()
            if parent is None:
                raise baseline_runs.Refused("original baseline session unavailable")
            run_id = str(parent["run_id"])
            binding = baseline_runs.assert_live(conn, run_id=run_id)
            if binding is None or str(binding["regression_attempt_id"]) != self.claim.attempt_id:
                raise baseline_runs.Refused("reader handoff differs from original baseline session")
            bound = conn.execute(
                "SELECT extract(epoch FROM (least(e.expires_at,a.lease_expires_at,"
                "(m.canonical_manifest->>'expiresAt')::timestamptz)-clock_timestamp())) AS seconds "
                "FROM baseline_regression_attempt a JOIN baseline_endpoint e ON e.attempt_id=a.id "
                "JOIN sealed_manifest m ON m.run_id=a.run_id AND m.workspace_id=a.workspace_id "
                "WHERE a.id=%s",
                (self.claim.attempt_id,),
            ).fetchone()
            ttl = 0 if bound is None else min(60, int(bound["seconds"]) - 1)
            if ttl < 1:
                raise baseline_runs.Refused(
                    "baseline endpoint has no remaining reader admission window"
                )
            lease = runners.admit_lease(
                conn,
                workspace_id=self.workspace_id,
                runner_id=runner_id,
                run_id=run_id,
                attempt_id=attempt_id,
                ttl_seconds=ttl,
            )
        return lease  # Commit succeeds before the controller can obtain this handoff.
