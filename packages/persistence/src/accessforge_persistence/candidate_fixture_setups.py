"""Pre-seed build-worker intent and independent initial fixture measurement.

Only the trusted regression coordinator calls these writers. Receipts do not approve a
candidate reader run, extend endpoint lifetime, or attest a desktop environment.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

import psycopg

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import candidate_builds as builds
from . import candidate_regressions as regressions

Refused = regressions.Refused


def _context(
    conn: psycopg.Connection[dict[str, Any]], claim: regressions.RegressionClaim, nonce: str
) -> dict[str, Any]:
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
        raise Refused("candidate fixture nonce is invalid")
    parent = regressions._owned(conn, claim, "DISPATCHED")
    regressions._authority(conn, claim.build_id, str(parent["workspace_id"]))
    if (
        not parent["endpoint_required"]
        or conn.execute(
            "SELECT 1 FROM candidate_endpoint WHERE attempt_id=%s", (claim.attempt_id,)
        ).fetchone()
    ):
        raise Refused("candidate fixture must precede endpoint planning")
    rows = conn.execute(
        "SELECT role,container_id,image_id,state FROM candidate_regression_process "
        "WHERE attempt_id=%s FOR SHARE",
        (claim.attempt_id,),
    ).fetchall()
    processes = {row["role"]: row for row in rows}
    if set(processes) != {"database", "driver", "candidate"} or any(
        row["state"] != "CREATED" or not row["container_id"] for row in rows
    ):
        raise Refused("candidate fixture requires the three original live processes")
    if any(processes[role]["image_id"] != parent["image_id"] for role in ("driver", "candidate")):
        raise Refused("candidate fixture image differs from original launch")
    return {
        "workspaceId": str(parent["workspace_id"]),
        "regressionAttemptId": claim.attempt_id,
        "buildId": claim.build_id,
        "workerEpoch": claim.epoch,
        "artifactDigest": parent["artifact_digest"],
        "runtimePolicyDigest": parent["policy_digest"],
        "imageId": parent["image_id"],
        "daemonId": parent["daemon_id"],
        "processes": {role: processes[role]["container_id"] for role in sorted(processes)},
        "nonce": nonce,
        "templateDigest": REFERENCE_FIXTURE_DIGEST,
        "variant": "inaccessible",
    }


def reserve(
    conn: psycopg.Connection[dict[str, Any]], *, claim: regressions.RegressionClaim, nonce: str
) -> dict[str, Any]:
    """One intent per attempt. Caller must commit before HTTP; no implicit recovery/reseed."""
    with conn.transaction():
        context = _context(conn, claim, nonce)
        row = conn.execute(
            "INSERT INTO candidate_fixture_reservation "
            "(regression_attempt_id,workspace_id,context,context_digest) "
            "VALUES(%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING *",
            (claim.attempt_id, context["workspaceId"], json.dumps(context), digest(context)),
        ).fetchone()
        if row is None:
            raise Refused("candidate fixture intent already exists; never implicitly replace it")
        return _view(row)


def confirm(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    context_digest: str,
    application: dict[str, Any],
) -> dict[str, Any]:
    with conn.transaction():
        # Lock the parent before its child, matching reservation and endpoint lifecycle order.
        regressions._owned(conn, claim, "DISPATCHED")
        row = conn.execute(
            "SELECT * FROM candidate_fixture_reservation WHERE regression_attempt_id=%s FOR UPDATE",
            (claim.attempt_id,),
        ).fetchone()
        if row is None:
            raise Refused("original candidate fixture reservation missing")
        original = _view(row)
        context = _context(conn, claim, original["context"]["nonce"])
        expected = {k: context[k] for k in ("nonce", "templateDigest", "variant")}
        if (
            row["observation"] is not None
            or digest(context) != context_digest
            or row["context_digest"] != context_digest
            or set(application) != set(expected) | {"createdAt", "observedAt", "effectCount"}
            or any(application[k] != value for k, value in expected.items())
            or type(application.get("effectCount")) is not int
            or application["effectCount"] != 0
        ):
            raise Refused("candidate initial fixture identity or empty state differs")
        try:
            created = parse_rfc3339_utc(application["createdAt"])
            observed = parse_rfc3339_utc(application["observedAt"])
        except (ValueError, TypeError) as exc:
            raise Refused("candidate fixture observation time unavailable") from exc
        now = builds._moment(conn, None)
        if not row["created_at"] <= created <= observed <= now + timedelta(seconds=5):
            raise Refused("candidate fixture predates reservation or has impossible chronology")
        if observed < now - timedelta(seconds=10):
            raise Refused("candidate initial fixture observation is stale")
        observation = {
            "contextDigest": context_digest,
            "application": application,
            "meaning": "INDEPENDENT_INITIAL_CANDIDATE_FIXTURE_NOT_READER_ATTESTATION",
        }
        result = conn.execute(
            "UPDATE candidate_fixture_reservation SET observation=%s::jsonb,observation_digest=%s,"
            "observed_at=clock_timestamp() WHERE regression_attempt_id=%s RETURNING *",
            (json.dumps(observation), digest(observation), claim.attempt_id),
        ).fetchone()
        assert result is not None
        return _view(result)


def _view(row: dict[str, Any]) -> dict[str, Any]:
    context, observation = row["context"], row["observation"]
    if (
        digest(context) != row["context_digest"]
        or context.get("workspaceId") != str(row["workspace_id"])
        or context.get("regressionAttemptId") != str(row["regression_attempt_id"])
        or (
            observation is not None
            and (
                digest(observation) != row["observation_digest"]
                or observation.get("contextDigest") != row["context_digest"]
            )
        )
    ):
        raise Refused("candidate fixture receipt integrity unavailable")
    return {
        "context": context,
        "contextDigest": row["context_digest"],
        "observation": observation,
        "observationDigest": row["observation_digest"],
        "reservedAt": to_rfc3339_utc(row["created_at"]),
        "confirmedAt": None if row["observed_at"] is None else to_rfc3339_utc(row["observed_at"]),
    }


def read(conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str) -> dict[str, Any] | None:
    """Workspace-scoped history only; never refreshes worker, endpoint or reader authority."""
    row = conn.execute(
        "SELECT * FROM candidate_fixture_reservation WHERE regression_attempt_id=%s", (attempt_id,)
    ).fetchone()
    return None if row is None else _view(row)


def for_run(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> dict[str, Any] | None:
    """Recheck original seed lineage for retention, including after endpoint cleanup.

    This is deliberately not a live authority check. A caller attaching the receipt before
    dispatch must separately check candidate_runs.assert_live and exact execution approval.
    """
    bound = conn.execute(
        "SELECT b.*,e.plan,e.binding_digest,e.origin,a.artifact_digest,a.policy_digest,"
        "a.image_id,a.daemon_id FROM candidate_run_binding b "
        "JOIN candidate_endpoint e ON e.attempt_id=b.regression_attempt_id "
        "AND e.workspace_id=b.workspace_id JOIN candidate_regression_attempt a "
        "ON a.id=b.regression_attempt_id AND a.workspace_id=b.workspace_id WHERE b.run_id=%s",
        (run_id,),
    ).fetchone()
    if bound is None:
        return None
    # A preview can POST before there is a reader binding. Its initial empty observation
    # must never be promoted into fresh-reader setup after those requests. Every gateway
    # request records its pre-request artifact sample, including failed/unknown deliveries.
    if conn.execute(
        "SELECT 1 FROM candidate_artifact_observation WHERE regression_attempt_id=%s "
        "AND payload->>'runId' IS NULL LIMIT 1",
        (bound["regression_attempt_id"],),
    ).fetchone():
        raise Refused("preview-observed candidate fixture cannot become fresh reader setup")
    receipt = read(conn, attempt_id=str(bound["regression_attempt_id"]))
    if receipt is None or receipt["observation"] is None:
        raise Refused("original confirmed candidate fixture evidence unavailable")
    context, observation = receipt["context"], receipt["observation"]
    expected = {
        "workspaceId": str(bound["workspace_id"]),
        "regressionAttemptId": str(bound["regression_attempt_id"]),
        "buildId": str(bound["build_id"]),
        "artifactDigest": bound["artifact_digest"],
        "runtimePolicyDigest": bound["policy_digest"],
        "imageId": bound["image_id"],
        "daemonId": bound["daemon_id"],
        "nonce": bound["fixture_nonce"],
        "templateDigest": bound["fixture_template_digest"],
    }
    if (
        any(context.get(key) != value for key, value in expected.items())
        or bound["binding_digest"] != bound["endpoint_binding_digest"]
        or bound["plan"]["path"] != "/form/" + context["nonce"]
        or context["processes"]["candidate"] != bound["plan"]["candidateId"]
        or context["processes"]["driver"] != bound["plan"]["driverId"]
        or observation.get("meaning")
        != "INDEPENDENT_INITIAL_CANDIDATE_FIXTURE_NOT_READER_ATTESTATION"
    ):
        raise Refused("candidate fixture receipt differs from original run lineage")
    return receipt
