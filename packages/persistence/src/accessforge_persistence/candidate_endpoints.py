"""Durable endpoint lifecycle under the exact build-linked regression worker's authority."""

from __future__ import annotations

import json
from typing import Any

import psycopg

from accessforge_domain.candidate_endpoint import validate_endpoint_origin, validate_endpoint_plan
from accessforge_domain.canonical import digest

from . import candidate_builds as builds
from . import candidate_regressions as regressions

Refused = regressions.Refused


def plan(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    identity: dict[str, Any],
) -> None:
    validate_endpoint_plan(identity)
    with conn.transaction():
        row = regressions._owned(conn, claim, "DISPATCHED")
        regressions._authority(conn, claim.build_id, str(row["workspace_id"]))
        if not row["endpoint_required"]:
            raise Refused("this regression was not dispatched with an endpoint")
        for key, expected in {
            "taskId": claim.attempt_id,
            "artifactDigest": row["artifact_digest"],
            "runtimePolicyDigest": row["policy_digest"],
            "imageId": row["image_id"],
            "daemonEndpoint": row["daemon_endpoint"],
            "daemonId": row["daemon_id"],
        }.items():
            if identity[key] != expected:
                raise Refused("endpoint differs from exact regression identity: " + key)
        processes = conn.execute(
            "SELECT role,container_id,image_id,state FROM candidate_regression_process "
            "WHERE attempt_id=%s FOR SHARE",
            (claim.attempt_id,),
        ).fetchall()
        by_role = {p["role"]: p for p in processes}
        if set(by_role) != {"database", "driver", "candidate"}:
            raise Refused("endpoint requires exactly the three observed runtime processes")
        if any(p["state"] != "CREATED" for p in processes):
            raise Refused("endpoint runtime process was not created or was already removed")
        for role, key in (("driver", "driverId"), ("candidate", "candidateId")):
            if (
                by_role[role]["container_id"] != identity[key]
                or by_role[role]["image_id"] != row["image_id"]
            ):
                raise Refused("endpoint process does not match original launch")
        regressions._owned(conn, claim, "DISPATCHED")
        inserted = conn.execute(
            "INSERT INTO candidate_endpoint (attempt_id,workspace_id,plan,state,expires_at) "
            "VALUES (%s,%s,%s::jsonb,'PLANNED',clock_timestamp()+%s*interval '1 second') "
            "ON CONFLICT (attempt_id) DO NOTHING RETURNING attempt_id",
            (claim.attempt_id, row["workspace_id"], json.dumps(identity), identity["wallSeconds"]),
        ).fetchone()
        if inserted is None:
            raise Refused("endpoint intent already exists; never implicitly replace it")


def _record(
    conn: psycopg.Connection[dict[str, Any]], claim: regressions.RegressionClaim
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM candidate_endpoint WHERE attempt_id=%s FOR UPDATE",
        (claim.attempt_id,),
    ).fetchone()
    if row is None:
        raise Refused("endpoint intent is absent or outside this workspace")
    return row


def bound(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    receipt: dict[str, Any],
) -> None:
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        regressions._authority(conn, claim.build_id, str(parent["workspace_id"]))
        row = _record(conn, claim)
        if row["state"] != "PLANNED" or row["expires_at"] <= builds._moment(conn, None):
            raise Refused("endpoint intent expired or was already bound/fenced")
        identity = {k: v for k, v in receipt.items() if k not in {"origin", "bindingDigest"}}
        if identity != row["plan"] or set(receipt) != set(identity) | {"origin", "bindingDigest"}:
            raise Refused("bound endpoint does not match its committed intent")
        validate_endpoint_origin(receipt["origin"])
        if receipt["bindingDigest"] != digest({**identity, "origin": receipt["origin"]}):
            raise Refused("endpoint receipt digest differs from actual binding")
        regressions._owned(conn, claim, "DISPATCHED")
        conn.execute(
            "UPDATE candidate_endpoint SET state='BOUND',origin=%s,receipt=%s::jsonb,"
            "binding_digest=%s,bound_at=clock_timestamp() WHERE attempt_id=%s",
            (receipt["origin"], json.dumps(receipt), receipt["bindingDigest"], claim.attempt_id),
        )


def assert_live(
    conn: psycopg.Connection[dict[str, Any]], *, claim: regressions.RegressionClaim
) -> None:
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        regressions._authority(conn, claim.build_id, str(parent["workspace_id"]))
        row = _record(conn, claim)
        if row["state"] != "BOUND" or row["expires_at"] <= builds._moment(conn, None):
            raise Refused("endpoint is expired, unbound, closed or fenced")
        regressions._owned(conn, claim, "DISPATCHED")


def closed(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    cleanup_confirmed: bool,
) -> None:
    # Cleanup observations survive an epoch fence, but cannot reactivate the endpoint or parent.
    with conn.transaction():
        parent = conn.execute(
            "SELECT worker_token,build_id,state,epoch FROM candidate_regression_attempt "
            "WHERE id=%s FOR UPDATE",
            (claim.attempt_id,),
        ).fetchone()
        if (
            parent is None
            or str(parent["worker_token"]) != claim.worker_token
            or str(parent["build_id"]) != claim.build_id
        ):
            raise Refused("endpoint cleanup belongs to another worker or build")
        row = _record(conn, claim)
        if row["cleanup_confirmed"]:
            return
        state = (
            "UNKNOWN"
            if (
                row["state"] == "UNKNOWN"
                or parent["state"] != "DISPATCHED"
                or parent["epoch"] != claim.epoch
                or not cleanup_confirmed
            )
            else "CLOSED"
        )
        if row["state"] == "UNKNOWN" and not cleanup_confirmed:
            return
        conn.execute(
            "UPDATE candidate_endpoint SET state=%s,cleanup_confirmed=%s,"
            "closed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE attempt_id=%s",
            (state, cleanup_confirmed, cleanup_confirmed, claim.attempt_id),
        )
