"""Read original evaluated regression evidence for one exact approved-patch verification."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.identity import IdentityKind, revalidate

from . import evaluations, functional_regression_evidence

REQUIRED_CHECKS = functional_regression_evidence.VALIDATION_CHECKS | frozenset(
    {
        "private_network_canary",
        "candidate_ddl_and_admin_denied",
        "fixture_creation",
        "fixture_definition_identity",
        "durable_fixture",
        "independent_fixture_definition",
        "form_available",
        "valid_submission",
        "exact_independent_database_receipt",
        "duplicate_conflict",
        "unknown_fixture_refused",
        "exactly_one_after_retries",
        "fixture_survives_restart",
        "restart_preserves_duplicate_guard",
        "durable_receipt_after_stop",
        "oracle_schema_outside_candidate_authority",
    }
)
AUTHORIZATION_CHECKS = (
    "fixture_creation_authorization_",
    "unauthorized_fixture_no_write_",
    "observer_authorization_",
    "reset_authorization_",
    "unauthorized_reset_preserves_",
)


def _evaluated_pair(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    receipt: dict[str, Any],
    functional_digest: str,
) -> bool:
    """Pure check of already authenticated original rows; never a public attestation API."""
    if baseline.get("outcome") != "FAIL" or candidate.get("outcome") not in {"PASS", "FAIL"}:
        return False
    for snapshot in (baseline, candidate):
        sealed, observed = snapshot.get("sealedIdentities"), snapshot.get("observedIdentities")
        if not isinstance(sealed, dict) or not isinstance(observed, dict):
            return False
        if any(not isinstance(v, str) or not v for v in [*sealed.values(), *observed.values()]):
            return False
        try:
            if not revalidate(
                {IdentityKind(k): v for k, v in sealed.items()},
                {IdentityKind(k): v for k, v in observed.items()},
            ).bound:
                return False
        except (TypeError, ValueError):
            return False
    if candidate["observedIdentities"].get("BUILD") != receipt.get("artifactDigest"):
        return False
    checks = receipt.get("checks")
    if not isinstance(checks, list) or any(not isinstance(c, str) for c in checks):
        return False
    if len(set(checks)) != len(checks) or not REQUIRED_CHECKS.issubset(checks):
        return False
    if any(
        len([c for c in checks if c.startswith(prefix) and c[len(prefix) :].isdigit()]) != 3
        for prefix in AUTHORIZATION_CHECKS
    ):
        return False
    producer = receipt.get("producerReceipt")
    if not isinstance(producer, dict) or not isinstance(producer.get("runEvidence"), dict):
        return False
    original = producer["runEvidence"]
    if (
        original.get("runId") != candidate.get("runId")
        or original.get("manifestDigest") != candidate.get("manifestDigest")
        or original.get("assertionSetDigest")
        != candidate["observedIdentities"].get("ASSERTION_SET")
    ):
        return False
    outcomes = candidate.get("assertions")
    if not isinstance(outcomes, list) or any(not isinstance(a, dict) for a in outcomes):
        return False
    functional = [a for a in outcomes if a.get("kind") == "FUNCTIONAL_VALIDATION"]
    return bool(functional) and all(
        a.get("condition") == "TRUE"
        and a.get("provenance") == "OBSERVER_AUTHORED"
        and a.get("evidenceRefs") == [functional_digest]
        and any(
            isinstance(original_a, dict)
            and original_a.get("assertionId") == a.get("assertionId")
            and original_a.get("kind") == "FUNCTIONAL_VALIDATION"
            and original_a.get("condition") == "TRUE"
            and original_a.get("provenance") == "OBSERVER_AUTHORED"
            for original_a in original.get("assertionObservations", [])
        )
        for a in functional
    )


def _retained(conn: psycopg.Connection[Any], snapshot: dict[str, Any]) -> bool:
    artifacts = snapshot.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False
    for item in artifacts:
        row = conn.execute(
            "SELECT kind,producer_id,content_digest,manifest_digest,state,retention "
            "FROM evidence_artifact WHERE id=%s AND run_id=%s AND attempt_id=%s FOR SHARE",
            (item["artifactId"], snapshot["runId"], snapshot["attemptId"]),
        ).fetchone()
        if row is None or (
            row["kind"] != item["kind"]
            or row["producer_id"] != item["producerId"]
            or row["content_digest"] != item["digest"]
            or row["manifest_digest"] != snapshot["manifestDigest"]
            or row["state"] != "PROMOTED"
            or row["retention"] != "RETAINED"
        ):
            return False
    return True


def attest(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    verification_id: str,
    baseline_run_id: str,
) -> bool:
    binding = conn.execute(
        "SELECT b.run_id,b.workspace_id FROM candidate_run_binding b "
        "JOIN patch_verification v ON v.id=b.verification_id AND v.workspace_id=b.workspace_id "
        "WHERE b.run_id=%s AND b.verification_id=%s AND v.baseline_run_id=%s",
        (run_id, verification_id, baseline_run_id),
    ).fetchone()
    if binding is None:
        return False
    baseline_record = evaluations.read(conn, run_id=baseline_run_id)
    candidate_record = evaluations.read(conn, run_id=run_id)
    if baseline_record is None or candidate_record is None:
        return False
    baseline, candidate = baseline_record["snapshot"], candidate_record["snapshot"]
    for snapshot in (baseline, candidate):
        current = conn.execute(
            "SELECT r.status,r.outcome,r.manifest_digest FROM run r JOIN sealed_manifest s "
            "ON s.run_id=r.id AND s.manifest_digest=r.manifest_digest "
            "WHERE r.id=%s AND r.workspace_id=%s",
            (snapshot["runId"], binding["workspace_id"]),
        ).fetchone()
        if current is None or current != {
            "status": "COMPLETED",
            "outcome": snapshot["outcome"],
            "manifest_digest": snapshot["manifestDigest"],
        }:
            return False
    receipt = functional_regression_evidence.for_run(conn, run_id=run_id)
    if receipt is None:
        return False
    session = {
        "workspace_id": binding["workspace_id"],
        "run_id": run_id,
        "attempt_id": candidate["attemptId"],
        "manifest_digest": candidate["manifestDigest"],
        "lease_id": receipt["leaseId"],
        "epoch": receipt["leaseEpoch"],
    }
    bundle = functional_regression_evidence.snapshot(conn, session)
    functional_digest = digest(bundle)
    matches = [
        a for a in candidate.get("artifacts", []) if a.get("kind") == "FUNCTIONAL_REGRESSION"
    ]
    if len(matches) != 1 or matches[0].get("digest") != functional_digest:
        return False
    return (
        _evaluated_pair(baseline, candidate, receipt, functional_digest)
        and _retained(conn, baseline)
        and _retained(conn, candidate)
    )
