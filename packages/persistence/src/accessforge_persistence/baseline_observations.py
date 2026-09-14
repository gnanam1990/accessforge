"""Private, immutable build-worker receipts; never canonical evidence-producer authority."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import baseline_endpoints as endpoints
from . import baseline_regressions as regressions
from . import baseline_runs
from .candidate_regressions import RegressionClaim

Refused = regressions.Refused


def retain(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: RegressionClaim,
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Bind measured bytes to current worker/endpoint and original baseline-reader context.

    Must be called by the trusted measuring coordinator, not a public upload route. The receipt
    survives endpoint cleanup as history; it does not keep its authority or assert OS execution.
    """
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        workspace = str(parent["workspace_id"])
        build = regressions._authority(conn, claim.build_id)
        endpoints.assert_live(conn, claim=claim)
        baseline_runs.assert_request(conn, run_id=str(parent["run_id"]), method="GET")
        endpoint = endpoints._record(conn, claim)
        expected = {
            "taskId": claim.attempt_id,
            "candidateId": endpoint["plan"]["candidateId"],
            "imageId": parent["image_id"],
            "daemonId": parent["daemon_id"],
            "artifactDigest": parent["artifact_digest"],
            "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
        }
        if (
            set(observation) != set(expected) | {"artifactTreeDigest", "observedAt"}
            or any(observation[k] != v for k, v in expected.items())
            or not isinstance(observation["artifactTreeDigest"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", observation["artifactTreeDigest"])
            or not isinstance(observation["observedAt"], str)
            or len(observation["observedAt"]) > 40
        ):
            raise Refused("exact deployed artifact observation required")
        try:
            captured = parse_rfc3339_utc(observation["observedAt"])
        except ValueError as exc:
            raise Refused("artifact observation time unavailable") from exc
        clock = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        assert clock is not None
        now = clock["now"]
        if (
            not max(endpoint["bound_at"], now - timedelta(seconds=10))
            <= captured
            <= now + timedelta(seconds=5)
        ):
            raise Refused("artifact observation is stale or precedes endpoint binding")
        context = conn.execute(
            "SELECT b.run_id,b.created_at,l.lease_id,l.lease_epoch,l.created_at AS lease_created "
            "FROM baseline_session_binding b LEFT JOIN baseline_reader_lease l "
            "USING(run_id,workspace_id) "
            "WHERE b.regression_attempt_id=%s",
            (claim.attempt_id,),
        ).fetchone()
        if context is None or (
            captured < context["created_at"]
            or (context["lease_created"] is not None and captured < context["lease_created"])
        ):
            raise Refused("baseline binding changed during artifact measurement")
        payload = {
            "runtimeKind": "BASELINE",
            "observation": observation,
            "workspaceId": workspace,
            "buildId": claim.build_id,
            "regressionAttemptId": claim.attempt_id,
            "workerEpoch": claim.epoch,
            "endpointBindingDigest": endpoint["binding_digest"],
            "runtimePolicyDigest": parent["policy_digest"],
            "runId": None if context is None else str(context["run_id"]),
            "leaseId": None
            if context is None or context["lease_id"] is None
            else str(context["lease_id"]),
            "leaseEpoch": None if context is None else context["lease_epoch"],
            "meaning": "BUILD_RECEIPT_CONTEXT_NOT_CANONICAL_EXECUTION_EVIDENCE",
        }
        archive = conn.execute(
            "SELECT retained_at,content_digest FROM baseline_archive WHERE build_id=%s "
            "AND state='RETAINED'",
            (claim.build_id,),
        ).fetchone()
        if (
            archive is None
            or archive["retained_at"] is None
            or archive["content_digest"] != observation["artifactDigest"]
            or not build["created_at"] <= build["finished_at"] <= archive["retained_at"] <= captured
        ):
            raise Refused("baseline source/build retention chronology unavailable")
        payload["baselineSourceLineage"] = {
            "meaning": "CAPTURED_BASELINE_INPUT_LINK_NOT_RUNTIME_SOURCE_READ",
            "workspaceId": workspace,
            "buildId": claim.build_id,
            "sourceSnapshotId": build["binding"]["source_snapshot_id"],
            "sourceTreeDigest": build["binding"]["source_tree_digest"],
            "sourceArchiveDigest": build["source_archive_digest"],
            "artifactDigest": build["artifact_digest"],
            "buildContainerId": build["container_id"],
            "imageId": build["image_id"],
            "daemonId": build["daemon_id"],
            "buildReservedAt": to_rfc3339_utc(build["created_at"]),
            "buildFinishedAt": to_rfc3339_utc(build["finished_at"]),
            "artifactRetainedAt": to_rfc3339_utc(archive["retained_at"]),
        }
        content_digest = digest(payload)
        identifier = str(
            uuid5(NAMESPACE_URL, "accessforge:baseline-artifact-observation:" + content_digest)
        )
        prior = conn.execute(
            "SELECT * FROM baseline_artifact_observation WHERE id=%s", (identifier,)
        ).fetchone()
        if prior is not None:
            return _view(prior)
        ordinal = conn.execute(
            "SELECT count(*)+1 AS next FROM baseline_artifact_observation "
            "WHERE regression_attempt_id=%s",
            (claim.attempt_id,),
        ).fetchone()
        if ordinal is None or ordinal["next"] > 256:
            raise Refused("artifact observation receipt budget exhausted")
        row = conn.execute(
            "INSERT INTO baseline_artifact_observation "
            "(id,workspace_id,regression_attempt_id,ordinal,receipt_digest,payload) "
            "VALUES(%s,%s,%s,%s,%s,%s::jsonb) RETURNING *",
            (
                identifier,
                workspace,
                claim.attempt_id,
                ordinal["next"],
                content_digest,
                json.dumps(payload),
            ),
        ).fetchone()
        assert row is not None
        return _view(row)


def _view(row: dict[str, Any]) -> dict[str, Any]:
    payload = row["payload"]
    if (
        not isinstance(payload, dict)
        or payload.get("runtimeKind") != "BASELINE"
        or digest(payload) != row["receipt_digest"]
        or payload.get("workspaceId") != str(row["workspace_id"])
        or payload.get("regressionAttemptId") != str(row["regression_attempt_id"])
        or str(row["id"])
        != str(
            uuid5(
                NAMESPACE_URL, "accessforge:baseline-artifact-observation:" + row["receipt_digest"]
            )
        )
    ):
        raise Refused("stored artifact receipt integrity unavailable")
    return {
        "receiptId": str(row["id"]),
        "receiptDigest": row["receipt_digest"],
        "ordinal": row["ordinal"],
        "recordedAt": to_rfc3339_utc(row["recorded_at"]),
        "receipt": payload,
    }


def list_for_attempt(
    conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str
) -> list[dict[str, Any]]:
    """Workspace-scoped historical read, not a live capability or authorization refresh."""
    rows = conn.execute(
        "SELECT * FROM baseline_artifact_observation "
        "WHERE regression_attempt_id=%s ORDER BY ordinal",
        (attempt_id,),
    ).fetchall()
    return [_view(row) for row in rows]


def for_runtime_preflight(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    session: dict[str, Any],
    dispatched_at: datetime,
    captured_at: datetime,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve server-owned measurement at the exact live action boundary, never browser JSON.

    Caller holds the authenticated session/run/lease locks and has revalidated current manual
    authority. No baseline binding or qualifying sample is missing evidence, not observed BUILD.
    """
    binding = conn.execute(
        "SELECT b.*,a.build_id,a.epoch,a.policy_digest,a.image_id,a.daemon_id,e.plan "
        "FROM baseline_session_binding b "
        "JOIN baseline_regression_attempt a ON a.id=b.regression_attempt_id "
        "JOIN baseline_endpoint e ON e.attempt_id=a.id WHERE b.run_id=%s",
        (session["run_id"],),
    ).fetchone()
    if binding is None:
        return None
    baseline_runs.assert_lease(
        conn,
        run_id=str(session["run_id"]),
        lease_id=str(session["lease_id"]),
        epoch=int(session["epoch"]),
    )
    for value in reversed(list_for_attempt(conn, attempt_id=str(binding["regression_attempt_id"]))):
        payload = value["receipt"]
        if any(
            payload.get(key) != expected
            for key, expected in {
                "workspaceId": str(session["workspace_id"]),
                "runId": str(session["run_id"]),
                "leaseId": str(session["lease_id"]),
                "leaseEpoch": int(session["epoch"]),
            }.items()
        ):
            continue  # Preview/earlier context cannot be attached to this runtime report.
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise Refused("retained artifact observation malformed")
        if any(
            payload.get(key) != expected
            for key, expected in {
                "buildId": str(binding["build_id"]),
                "workerEpoch": binding["epoch"],
                "runtimePolicyDigest": binding["policy_digest"],
                "endpointBindingDigest": binding["endpoint_binding_digest"],
                "meaning": "BUILD_RECEIPT_CONTEXT_NOT_CANONICAL_EXECUTION_EVIDENCE",
            }.items()
        ) or any(
            observation.get(key) != expected
            for key, expected in {
                "taskId": str(binding["regression_attempt_id"]),
                "imageId": binding["image_id"],
                "daemonId": binding["daemon_id"],
                "candidateId": binding["plan"]["candidateId"],
                "artifactDigest": manifest["buildArtifactDigest"],
                "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
            }.items()
        ):
            raise Refused("artifact receipt differs from current deployment binding")
        try:
            measured_at = parse_rfc3339_utc(observation["observedAt"])
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused("retained artifact measurement time unavailable") from exc
        if max(dispatched_at, captured_at - timedelta(seconds=10)) <= measured_at <= captured_at:
            return value
    return None
