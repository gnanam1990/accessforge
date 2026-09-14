"""Original queued-run fixture reservation for the owned protected baseline harness.

No network, reader or endpoint starts here. These callbacks are trusted orchestration capabilities,
not navigator tools. They reuse the original environment/fixture approval checks unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.candidate_endpoint import validate_endpoint_origin
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc
from accessforge_persistence import baseline_regressions as regressions
from accessforge_persistence.candidate_regressions import RegressionClaim

from . import reference_fixture_setup as setup

Refused = setup.Refused


def prepare_context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    reset_values: dict[str, str],
    observer_config: dict[str, str],
) -> dict[str, Any]:
    """Reserve the original nonce before runtime startup, not the application fixture itself."""
    validate_endpoint_origin(origin)
    if observer_config != {"effect": "CREATE_TEST_REQUEST"}:
        raise Refused("owned baseline harness requires the exact protected observer contract")
    if reset_values != {"variant": "inaccessible"}:
        raise Refused(
            "owned baseline harness requires the originally approved inaccessible variant"
        )
    with conn.transaction():
        return setup._context(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            origin=origin,
            reset_credential_ref=reset_credential_ref,
            observer_credential_ref=observer_credential_ref,
            reset_values=reset_values,
            observer_config=observer_config,
            create=True,
        )


def _live_context(
    conn: psycopg.Connection[Any], claim: RegressionClaim, context: dict[str, Any]
) -> dict[str, Any]:
    parent = regressions._owned(conn, claim, "DISPATCHED")
    if (
        str(parent["run_id"]) != context["runId"]
        or str(parent["workspace_id"]) != context["workspaceId"]
    ):
        raise Refused("baseline fixture belongs to another runtime or run")
    original = setup._context(
        conn,
        workspace_id=context["workspaceId"],
        run_id=context["runId"],
        origin=context["origin"],
        reset_credential_ref=context["resetCredentialRef"],
        observer_credential_ref=context["observerCredentialRef"],
        reset_values={"variant": "inaccessible"},
        observer_config={"effect": "CREATE_TEST_REQUEST"},
        create=False,
    )
    if original != context:
        raise Refused("baseline fixture changed from its original approved context")
    rows = conn.execute(
        "SELECT role,container_id,image_id,state FROM baseline_regression_process "
        "WHERE attempt_id=%s FOR SHARE",
        (claim.attempt_id,),
    ).fetchall()
    if {p["role"] for p in rows} != {"database", "driver", "candidate"} or any(
        p["state"] != "CREATED"
        or not p["container_id"]
        or (p["role"] != "database" and p["image_id"] != parent["image_id"])
        for p in rows
    ):
        raise Refused("baseline seed requires its three original live processes")
    regressions.assert_active(conn, claim=claim)
    return {
        "attemptId": claim.attempt_id,
        "buildId": claim.build_id,
        "workerEpoch": claim.epoch,
        "artifactDigest": parent["artifact_digest"],
        "policyDigest": parent["policy_digest"],
        "daemonEndpoint": parent["daemon_endpoint"],
        "daemonId": parent["daemon_id"],
        "processes": {
            p["role"]: {"containerId": p["container_id"], "imageId": p["image_id"]} for p in rows
        },
    }


def reserve(
    conn: psycopg.Connection[Any], *, claim: RegressionClaim, context: dict[str, Any], nonce: str
) -> str:
    """Caller must commit this one-use intent before the harness sends the setup HTTP request."""
    with conn.transaction():
        _live_context(conn, claim, context)
        if nonce != context["nonce"]:
            raise Refused("baseline runtime must seed the original reserved nonce")
        fingerprint = digest(context)
        inserted = conn.execute(
            "INSERT INTO fixture_setup_reservation(run_id,workspace_id,context,context_digest) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING RETURNING run_id",
            (context["runId"], context["workspaceId"], Jsonb(context), fingerprint),
        ).fetchone()
        if inserted is None:
            raise Refused("baseline fixture setup was already attempted; do not reseed")
        return fingerprint


def confirm(
    conn: psycopg.Connection[Any],
    *,
    claim: RegressionClaim,
    context: dict[str, Any],
    context_digest: str,
    application: dict[str, Any],
) -> dict[str, Any]:
    """Only independent protected-driver SQL measurements may enter this trusted writer."""
    with conn.transaction():
        runtime = _live_context(conn, claim, context)
        row = conn.execute(
            "SELECT * FROM fixture_setup_reservation WHERE run_id=%s FOR UPDATE",
            (context["runId"],),
        ).fetchone()
        expected = {k: context[k] for k in ("nonce", "templateDigest", "variant")}
        if (
            row is None
            or row["context"] != context
            or row["context_digest"] != context_digest
            or digest(context) != context_digest
            or row["observation"] is not None
            or set(application) != set(expected) | {"createdAt", "observedAt", "effectCount"}
            or any(application[k] != v for k, v in expected.items())
            or type(application.get("effectCount")) is not int
            or application["effectCount"] != 0
        ):
            raise Refused("baseline seed observation differs from its original reservation")
        created = parse_rfc3339_utc(application["createdAt"])
        observed = parse_rfc3339_utc(application["observedAt"])
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()
        assert now is not None
        if not row["created_at"] <= created <= observed <= now["now"] + timedelta(seconds=5) or (
            observed < now["now"] - timedelta(seconds=10)
        ):
            raise Refused("baseline fixture observation predates reservation or is stale")
        observation = {
            "meaning": "INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION",
            "contextDigest": context_digest,
            "setupHttpStatus": 201,
            "application": application,
            "reservedAt": to_rfc3339_utc(row["created_at"]),
            "baselineRuntime": runtime,
            "baselineRuntimeDigest": digest(runtime),
        }
        regressions.assert_active(conn, claim=claim)
        conn.execute(
            "UPDATE fixture_setup_reservation SET observation=%s,observation_digest=%s,"
            "observed_at=clock_timestamp() WHERE run_id=%s",
            (Jsonb(observation), digest(observation), context["runId"]),
        )
        return observation
