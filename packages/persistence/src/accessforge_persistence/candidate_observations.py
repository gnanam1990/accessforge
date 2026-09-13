"""Private, immutable build-worker receipts; never canonical evidence-producer authority."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import candidate_builds as builds
from . import candidate_endpoints as endpoints
from . import candidate_regressions as regressions
from . import candidate_runs

Refused = regressions.Refused


def retain(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    observation: dict[str, Any],
) -> dict[str, Any]:
    """Bind measured bytes to current worker/endpoint and optional candidate-reader context.

    Must be called by the trusted measuring coordinator, not a public upload route. The receipt
    survives endpoint cleanup as history; it does not keep its authority or assert OS execution.
    """
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        workspace = str(parent["workspace_id"])
        regressions._authority(conn, claim.build_id, workspace)
        endpoints.assert_live(conn, claim=claim)
        candidate_runs.assert_request(conn, attempt_id=claim.attempt_id, method="GET")
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
        now = builds._moment(conn, None)
        if (
            not max(endpoint["bound_at"], now - timedelta(seconds=10))
            <= captured
            <= now + timedelta(seconds=5)
        ):
            raise Refused("artifact observation is stale or precedes endpoint binding")
        context = conn.execute(
            "SELECT b.run_id,b.created_at,l.lease_id,l.lease_epoch,l.created_at AS lease_created "
            "FROM candidate_run_binding b LEFT JOIN candidate_reader_lease l "
            "USING(run_id,workspace_id) "
            "WHERE b.regression_attempt_id=%s",
            (claim.attempt_id,),
        ).fetchone()
        if context is not None and (
            captured < context["created_at"]
            or (context["lease_created"] is not None and captured < context["lease_created"])
        ):
            raise Refused("candidate binding changed during artifact measurement")
        payload = {
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
        content_digest = digest(payload)
        identifier = str(uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + content_digest))
        prior = conn.execute(
            "SELECT * FROM candidate_artifact_observation WHERE id=%s", (identifier,)
        ).fetchone()
        if prior is not None:
            return _view(prior)
        ordinal = conn.execute(
            "SELECT count(*)+1 AS next FROM candidate_artifact_observation "
            "WHERE regression_attempt_id=%s",
            (claim.attempt_id,),
        ).fetchone()
        if ordinal is None or ordinal["next"] > 256:
            raise Refused("artifact observation receipt budget exhausted")
        row = conn.execute(
            "INSERT INTO candidate_artifact_observation "
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
        or digest(payload) != row["receipt_digest"]
        or payload.get("workspaceId") != str(row["workspace_id"])
        or payload.get("regressionAttemptId") != str(row["regression_attempt_id"])
        or str(row["id"])
        != str(uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + row["receipt_digest"]))
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
        "SELECT * FROM candidate_artifact_observation "
        "WHERE regression_attempt_id=%s ORDER BY ordinal",
        (attempt_id,),
    ).fetchall()
    return [_view(row) for row in rows]
