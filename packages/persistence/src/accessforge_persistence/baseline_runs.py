"""Bind the original baseline to one endpoint and one reader lease, never a new approval."""

from __future__ import annotations

from typing import Any

import psycopg

from . import baseline_builds, baseline_endpoints
from .candidate_regressions import RegressionClaim

Refused = baseline_builds.Refused


def reader_cleanup_confirmed(conn: psycopg.Connection[Any], *, attempt_id: str) -> bool:
    """Historical stop proof, not live authority. Expiry/release alone is insufficient."""
    return (
        conn.execute(
            "SELECT 1 FROM baseline_session_binding s JOIN baseline_reader_lease b "
            "USING(run_id,workspace_id) LEFT JOIN desktop_lease l "
            "ON l.id=b.lease_id AND l.workspace_id=b.workspace_id "
            "WHERE s.regression_attempt_id=%s AND (l.id IS NULL OR l.run_id<>b.run_id "
            "OR l.epoch<>b.lease_epoch OR l.released_at IS NULL "
            "OR l.stop_acknowledged_at IS NULL "
            "OR l.stop_acknowledged_epoch IS DISTINCT FROM b.lease_epoch "
            "OR l.release_reason IS DISTINCT FROM 'STOP_ACKNOWLEDGED')",
            (attempt_id,),
        ).fetchone()
        is None
    )


def assert_reader_released(conn: psycopg.Connection[Any], *, attempt_id: str) -> None:
    if not reader_cleanup_confirmed(conn, attempt_id=attempt_id):
        raise Refused("baseline reader lacks original-epoch stop acknowledgement")


def prepare(conn: psycopg.Connection[Any], *, claim: RegressionClaim) -> None:
    with conn.transaction():
        baseline_endpoints.assert_live(conn, claim=claim)
        parent = conn.execute(
            "SELECT run_id,workspace_id FROM baseline_regression_attempt WHERE id=%s",
            (claim.attempt_id,),
        ).fetchone()
        assert parent is not None
        binding = baseline_builds.read_binding(
            conn, workspace_id=str(parent["workspace_id"]), run_id=str(parent["run_id"])
        )
        baseline_endpoints.assert_live(conn, claim=claim)
        row = conn.execute(
            "INSERT INTO baseline_session_binding(run_id,workspace_id,regression_attempt_id,"
            "manifest_digest,endpoint_binding_digest) SELECT %s,%s,attempt_id,%s,binding_digest "
            "FROM baseline_endpoint WHERE attempt_id=%s AND state='BOUND' "
            "AND expires_at>clock_timestamp() ON CONFLICT DO NOTHING RETURNING run_id",
            (
                parent["run_id"],
                parent["workspace_id"],
                binding["manifest_digest"],
                claim.attempt_id,
            ),
        ).fetchone()
        if row is None:
            raise Refused("baseline session already bound or endpoint expired")


def assert_live(conn: psycopg.Connection[Any], *, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT s.*,a.build_id,a.worker_token,a.epoch,e.binding_digest,e.expires_at "
        "FROM baseline_session_binding s JOIN baseline_regression_attempt a "
        "ON a.id=s.regression_attempt_id AND a.workspace_id=s.workspace_id "
        "JOIN baseline_endpoint e ON e.attempt_id=a.id AND e.workspace_id=s.workspace_id "
        "WHERE s.run_id=%s",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    if row["endpoint_binding_digest"] != row["binding_digest"]:
        raise Refused("baseline endpoint differs from its bound session")
    baseline_endpoints.assert_live(
        conn,
        claim=RegressionClaim(
            str(row["regression_attempt_id"]),
            str(row["build_id"]),
            str(row["worker_token"]),
            row["epoch"],
        ),
    )
    return dict(row)


def assert_lease(conn: psycopg.Connection[Any], *, run_id: str, lease_id: str, epoch: int) -> None:
    binding = assert_live(conn, run_id=run_id)
    if binding is None:
        return
    row = conn.execute(
        "SELECT 1 FROM baseline_reader_lease b JOIN desktop_lease l ON l.id=b.lease_id "
        "AND l.workspace_id=b.workspace_id JOIN runner d ON d.id=l.runner_id "
        "AND d.workspace_id=b.workspace_id JOIN sealed_manifest m "
        "ON m.run_id=b.run_id AND m.workspace_id=b.workspace_id "
        "WHERE b.run_id=%s AND b.lease_id=%s AND b.lease_epoch=%s "
        "AND l.run_id=b.run_id AND l.epoch=b.lease_epoch AND d.lease_epoch=b.lease_epoch "
        "AND d.profile_digest=m.runner_profile_digest AND d.revoked_at IS NULL "
        "AND d.quarantined_at IS NULL AND l.released_at IS NULL "
        "AND l.cancel_requested_at IS NULL AND l.deadline_at>clock_timestamp() "
        "AND l.deadline_at<=%s "
        "AND l.deadline_at<=(m.canonical_manifest->>'expiresAt')::timestamptz",
        (run_id, lease_id, epoch, binding["expires_at"]),
    ).fetchone()
    if row is None:
        raise Refused("baseline dispatch requires its original live reader lease and profile")


def assert_request(conn: psycopg.Connection[Any], *, run_id: str, method: str) -> None:
    """No method-only authorization for effects; committed action delivery is required next."""
    if assert_live(conn, run_id=run_id) is None:
        raise Refused("baseline request has no bound session")
    row = conn.execute(
        "SELECT lease_id,lease_epoch FROM baseline_reader_lease WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    if row is not None:
        assert_lease(conn, run_id=run_id, lease_id=str(row["lease_id"]), epoch=row["lease_epoch"])
    if method != "GET":
        raise Refused("baseline effects require committed one-shot action delivery")
