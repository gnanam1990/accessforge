"""Owned endpoint receipts for the original approved baseline fixture, never reader approval."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.candidate_endpoint import validate_endpoint_origin, validate_endpoint_plan
from accessforge_domain.canonical import digest

from . import baseline_fixture_evidence
from . import baseline_regressions as regressions
from .candidate_regressions import RegressionClaim

Refused = regressions.Refused


def _seed(conn: psycopg.Connection[Any], parent: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT f.*,r.manifest_digest,i.id AS fixture_id,i.nonce,i.template_digest "
        "FROM fixture_setup_reservation f JOIN run r "
        "ON r.id=f.run_id AND r.workspace_id=f.workspace_id JOIN run_fixture_instance i "
        "ON i.run_id=f.run_id AND i.workspace_id=f.workspace_id "
        "WHERE f.run_id=%s FOR SHARE OF f,i",
        (parent["run_id"],),
    ).fetchone()
    if row is None or not isinstance(row["observation"], dict):
        raise Refused("baseline endpoint requires original confirmed fixture seed")
    context, observation = row["context"], row["observation"]
    application = observation.get("application")
    if (
        digest(context) != row["context_digest"]
        or digest(observation) != row["observation_digest"]
        or observation.get("contextDigest") != row["context_digest"]
        or context.get("runId") != str(parent["run_id"])
        or context.get("workspaceId") != str(parent["workspace_id"])
        or context.get("manifestDigest") != row["manifest_digest"]
        or context.get("fixtureId") != str(row["fixture_id"])
        or context.get("nonce") != row["nonce"]
        or context.get("templateDigest") != row["template_digest"]
        or "baselineRuntime" not in observation
        or observation.get("meaning") != "INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION"
        or row["observed_at"] is None
        or not isinstance(application, dict)
        or application.get("nonce") != row["nonce"]
        or application.get("templateDigest") != row["template_digest"]
        or application.get("variant") != context.get("variant")
        or type(application.get("effectCount")) is not int
        or application["effectCount"] != 0
    ):
        raise Refused("baseline seed provenance differs from the original run")
    baseline_fixture_evidence.validate_runtime(
        conn,
        run_id=str(parent["run_id"]),
        workspace_id=str(parent["workspace_id"]),
        observation=observation,
    )
    processes = conn.execute(
        "SELECT role,state FROM baseline_regression_process WHERE attempt_id=%s", (parent["id"],)
    ).fetchall()
    if {p["role"] for p in processes} != {"database", "driver", "candidate"} or any(
        p["state"] != "CREATED" for p in processes
    ):
        raise Refused("baseline endpoint needs its three original live processes")
    return dict(context)


def plan(
    conn: psycopg.Connection[Any], *, claim: RegressionClaim, identity: dict[str, Any]
) -> None:
    validate_endpoint_plan(identity)
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        if not parent["endpoint_required"]:
            raise Refused("baseline runtime was not reserved with an endpoint")
        context = _seed(conn, parent)
        expected = dict(
            taskId=claim.attempt_id,
            artifactDigest=parent["artifact_digest"],
            runtimePolicyDigest=parent["policy_digest"],
            imageId=parent["image_id"],
            daemonEndpoint=parent["daemon_endpoint"],
            daemonId=parent["daemon_id"],
            listenOrigin=context["origin"],
            path="/form/" + context["nonce"],
        )
        if any(identity.get(k) != v for k, v in expected.items()):
            raise Refused("baseline endpoint differs from original authority and fixture")
        rows = conn.execute(
            "SELECT role,container_id FROM baseline_regression_process WHERE attempt_id=%s",
            (claim.attempt_id,),
        ).fetchall()
        processes = {p["role"]: p["container_id"] for p in rows}
        if (
            identity["candidateId"] != processes["candidate"]
            or identity["driverId"] != processes["driver"]
        ):
            raise Refused("baseline endpoint process identity changed")
        regressions.assert_active(conn, claim=claim)
        row = conn.execute(
            "INSERT INTO baseline_endpoint(attempt_id,workspace_id,plan,state,expires_at) "
            "VALUES(%s,%s,%s,'PLANNED',clock_timestamp()+%s*interval '1 second') "
            "ON CONFLICT(attempt_id) DO NOTHING RETURNING attempt_id",
            (claim.attempt_id, parent["workspace_id"], Jsonb(identity), identity["wallSeconds"]),
        ).fetchone()
        if row is None:
            raise Refused("baseline endpoint already reserved; no implicit replacement")


def _record(conn: psycopg.Connection[Any], claim: RegressionClaim) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM baseline_endpoint WHERE attempt_id=%s FOR UPDATE", (claim.attempt_id,)
    ).fetchone()
    if row is None:
        raise Refused("baseline endpoint is unavailable")
    return dict(row)


def _fresh(conn: psycopg.Connection[Any], row: dict[str, Any], state: str) -> None:
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()
    assert now is not None
    if row["state"] != state or row["expires_at"] <= now["now"]:
        raise Refused("baseline endpoint is expired, closed or fenced")


def bound(
    conn: psycopg.Connection[Any], *, claim: RegressionClaim, receipt: dict[str, Any]
) -> None:
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        _seed(conn, parent)
        row = _record(conn, claim)
        identity = {k: v for k, v in receipt.items() if k not in {"origin", "bindingDigest"}}
        if identity != row["plan"] or set(receipt) != set(identity) | {"origin", "bindingDigest"}:
            raise Refused("baseline endpoint receipt differs from committed plan")
        validate_endpoint_origin(receipt["origin"])
        if receipt["origin"] != identity["listenOrigin"] or receipt["bindingDigest"] != digest(
            {**identity, "origin": receipt["origin"]}
        ):
            raise Refused("baseline endpoint bound another origin or receipt digest")
        regressions.assert_active(conn, claim=claim)
        _fresh(conn, row, "PLANNED")
        conn.execute(
            "UPDATE baseline_endpoint SET state='BOUND',origin=%s,receipt=%s,"
            "binding_digest=%s,bound_at=clock_timestamp() WHERE attempt_id=%s",
            (receipt["origin"], Jsonb(receipt), receipt["bindingDigest"], claim.attempt_id),
        )


def assert_live(conn: psycopg.Connection[Any], *, claim: RegressionClaim) -> None:
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        _seed(conn, parent)
        row = _record(conn, claim)
        regressions.assert_active(conn, claim=claim)
        _fresh(conn, row, "BOUND")


def closed(
    conn: psycopg.Connection[Any], *, claim: RegressionClaim, cleanup_confirmed: bool
) -> None:
    with conn.transaction():
        regressions._fact_owner(conn, claim)
        parent = conn.execute(
            "SELECT state,epoch FROM baseline_regression_attempt WHERE id=%s", (claim.attempt_id,)
        ).fetchone()
        assert parent is not None
        row = _record(conn, claim)
        if row["cleanup_confirmed"]:
            return
        if row["state"] == "UNKNOWN" and not cleanup_confirmed:
            return
        state = (
            "CLOSED"
            if (
                row["state"] != "UNKNOWN"
                and parent["state"] == "DISPATCHED"
                and parent["epoch"] == claim.epoch
                and cleanup_confirmed
            )
            else "UNKNOWN"
        )
        conn.execute(
            "UPDATE baseline_endpoint SET state=%s,cleanup_confirmed=%s,"
            "closed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE attempt_id=%s",
            (state, cleanup_confirmed, cleanup_confirmed, claim.attempt_id),
        )
