"""Worker-side assertion publication from original protected measurements and frozen inputs."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.evaluation.rules import functional_validation_assertions
from accessforge_domain.functional_validation import ValidationObservation

from . import baseline_runs, journeys


def receipt(
    conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str, observation: ValidationObservation
) -> dict[str, Any]:
    """Called inside the successful fenced finish transaction; never backfills old receipts.

    A regression without an original reader lease or executable assertion contract still has
    measurements, but cannot claim a run-bound assertion. Cleanup does not require live authority.
    """
    result: dict[str, Any] = {
        "format": "accessforge.functional-producer.v1",
        "validation": observation.canonical_form(),
        "runEvidence": None,
    }
    baseline_runs.assert_reader_released(conn, attempt_id=attempt_id)
    row = conn.execute(
        "SELECT b.run_id,b.workspace_id,s.canonical_manifest,s.manifest_digest,"
        "r.manifest_digest AS run_manifest_digest,c.lease_id,c.lease_epoch,"
        "l.epoch AS actual_epoch,l.run_id AS leased_run,l.released_at "
        "FROM baseline_session_binding b JOIN sealed_manifest s "
        "ON s.run_id=b.run_id AND s.workspace_id=b.workspace_id "
        "JOIN run r ON r.id=b.run_id AND r.workspace_id=b.workspace_id "
        "JOIN baseline_reader_lease c ON c.run_id=b.run_id AND c.workspace_id=b.workspace_id "
        "JOIN desktop_lease l ON l.id=c.lease_id AND l.workspace_id=c.workspace_id "
        "WHERE b.regression_attempt_id=%s",
        (attempt_id,),
    ).fetchone()
    if row is None:
        return result
    if (
        row["manifest_digest"] != row["run_manifest_digest"]
        or row["lease_epoch"] != row["actual_epoch"]
        or row["leased_run"] != row["run_id"]
        or row["released_at"] is None
    ):
        raise ValueError("functional producer lacks original manifest/released reader binding")
    manifest = row["canonical_manifest"]
    contract_row = conn.execute(
        "SELECT reviewer_summary FROM journey_version WHERE id=%s",
        (manifest["journeyVersionId"],),
    ).fetchone()
    if contract_row is None or "assertionContract" not in contract_row["reviewer_summary"]:
        return result  # Historical prose-only journeys do not acquire new predicates.
    assertions = journeys.load_assertion_contract(
        conn,
        version_id=manifest["journeyVersionId"],
        expected_digest=manifest["assertionSetDigest"],
    )
    result["runEvidence"] = {
        "workspaceId": str(row["workspace_id"]),
        "runId": str(row["run_id"]),
        "manifestDigest": row["manifest_digest"],
        "leaseId": str(row["lease_id"]),
        "leaseEpoch": row["lease_epoch"],
        "assertionSetDigest": manifest["assertionSetDigest"],
        "assertionObservations": functional_validation_assertions(assertions, observation),
    }
    return result
