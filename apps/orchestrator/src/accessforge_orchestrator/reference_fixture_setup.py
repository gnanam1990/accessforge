"""Queued-run fixture setup. No reader startup, browser action, migration or deployment.

Run as trusted setup infrastructure with a separate read-only application observer connection.
An uncertain request leaves its durable nonce pending; explicit retry reconciles that same nonce.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import psycopg
from psycopg.types.json import Jsonb

from accessforge_contracts.reference_fixture import (
    REFERENCE_FIXTURE_DIGEST,
    REFERENCE_FIXTURE_VERSION,
)
from accessforge_domain.canonical import digest
from accessforge_domain.origins import normalize_origin
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc
from accessforge_persistence import (
    execution_approvals,
    fixtures,
    journeys,
    projects,
    workspace_connection,
)
from accessforge_persistence.evidence.observer import ApplicationObserver


class Refused(RuntimeError):
    """No setup confirmation; no permission to dispatch a desktop."""


def _context(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    reset_values: dict[str, str],
    observer_config: dict[str, str],
    create: bool,
) -> dict[str, Any]:
    run = conn.execute("SELECT * FROM run WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
    if (
        run is None
        or str(run["workspace_id"]) != workspace_id
        or run["status"] != "QUEUED"
        or run["cancel_requested_at"] is not None
        or run["quarantined"]
        or conn.execute("SELECT 1 FROM desktop_lease WHERE run_id=%s", (run_id,)).fetchone()
    ):
        raise Refused("setup requires an approved queued run before any desktop lease")
    conn.execute("SELECT id FROM approval WHERE id=%s FOR SHARE", (run["authorization_id"],))
    seal = conn.execute("SELECT id FROM sealed_manifest WHERE run_id=%s", (run_id,)).fetchone()
    if seal is None:
        raise Refused("original execution seal unavailable")
    manifest = execution_approvals.assert_authorized(
        conn, sealed_manifest_id=str(seal["id"]), run_id=run_id, workspace_id=workspace_id
    )
    if (
        digest(manifest) != run["manifest_digest"]
        or str(run["authorization_id"]) != manifest["authorizationId"]
    ):
        raise Refused("run and approval bindings differ")
    environment = conn.execute(
        "SELECT e.* FROM environment_manifest e JOIN sealed_manifest s "
        "ON s.environment_manifest_id=e.id WHERE s.run_id=%s FOR SHARE OF e",
        (run_id,),
    ).fetchone()
    if (
        environment is None
        or environment["reset_credential_ref"] != reset_credential_ref
        or environment["observer_credential_ref"] != observer_credential_ref
        or reset_credential_ref == observer_credential_ref
        or environment["fixture_reset_strategy"] != "FRESH_FIXTURE_NONCE"
        or normalize_origin(origin)
        not in {normalize_origin(o) for o in environment["allowed_origins"]}
    ):
        raise Refused("setup configuration differs from the approved environment")
    configured_environment = projects.EnvironmentSpec(
        name=environment["name"],
        allowed_origins=frozenset(normalize_origin(o) for o in environment["allowed_origins"]),
        fixture_reset_strategy=environment["fixture_reset_strategy"],
        observer_credential_ref=environment["observer_credential_ref"],
        reset_credential_ref=environment["reset_credential_ref"],
        permitted_effects=frozenset(environment["permitted_effects"]),
        expires_at=to_rfc3339_utc(environment["expires_at"]),
    ).config_digest()
    if configured_environment != manifest["environmentConfigDigest"]:
        raise Refused("complete setup environment content differs from sealed identity")
    contract = journeys.load_fixture_contract(
        conn, version_id=manifest["journeyVersionId"], expected_digest=manifest["fixtureDigest"]
    )
    if (
        contract["templateId"] != "service-request"
        or set(reset_values) != {"variant"}
        or reset_values["variant"] not in {"accessible", "inaccessible", "missing-label-v1"}
        or digest(reset_values) != contract["resetValuesDigest"]
        or digest(observer_config) != contract["observerConfigDigest"]
        or observer_config.get("effect") != "CREATE_TEST_REQUEST"
    ):
        raise Refused("private fixture configuration differs from the original reviewed contract")
    fixture = conn.execute(
        "SELECT * FROM run_fixture_instance WHERE run_id=%s", (run_id,)
    ).fetchone()
    if fixture is None and create:
        fixtures.create_instance(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            template_id="service-request",
            template_digest=REFERENCE_FIXTURE_DIGEST,
            navigator_values=contract["navigatorValues"],
            observer_config=observer_config,
        )
        fixture = conn.execute(
            "SELECT * FROM run_fixture_instance WHERE run_id=%s", (run_id,)
        ).fetchone()
    expected = fixtures.contract_digest(
        template_id="service-request",
        template_digest=REFERENCE_FIXTURE_DIGEST,
        navigator_values=contract["navigatorValues"],
        observer_config=observer_config,
    )
    if (
        fixture is None
        or fixture["template_id"] != "service-request"
        or fixture["template_digest"] != REFERENCE_FIXTURE_DIGEST
        or fixture["navigator_values"] != contract["navigatorValues"]
        or fixture["observer_config"] != observer_config
        or fixture["captured_contract_digest"] != expected
    ):
        raise Refused("reserved fixture differs from the approved material")
    return {
        "workspaceId": workspace_id,
        "runId": run_id,
        "manifestDigest": digest(manifest),
        "fixtureId": str(fixture["id"]),
        "nonce": fixture["nonce"],
        "fixtureDigest": manifest["fixtureDigest"],
        "templateDigest": REFERENCE_FIXTURE_DIGEST,
        "variant": reset_values["variant"],
        "origin": origin,
        "environmentConfigDigest": configured_environment,
        "resetCredentialRef": reset_credential_ref,
        "observerCredentialRef": observer_credential_ref,
    }


_SETUP_DEADLINE_SECONDS = 10.0


def _provision(context: dict[str, Any], setup_token: str) -> int:
    return asyncio.run(_provision_async(context, setup_token))


async def _provision_async(context: dict[str, Any], setup_token: str) -> int:
    url = (
        context["origin"]
        + "/api/_test/fixtures?"
        + urlencode(
            {
                "variant": context["variant"],
                "nonce": context["nonce"],
            }
        )
    )
    # The total cancellation deadline includes connect, slow-drip headers and body.
    # No proxy, redirect or automatic retry may receive/replay the setup identity.
    async with asyncio.timeout(_SETUP_DEADLINE_SECONDS):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client:
            async with client.stream(
                "POST", url, headers={"x-setup-token": setup_token}
            ) as response:
                if response.status_code not in {200, 201}:
                    raise Refused("setup response unavailable")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise Refused("setup response oversized")
                if json.loads(body) != {
                    "nonce": context["nonce"],
                    "variant": context["variant"],
                    "template_digest": REFERENCE_FIXTURE_DIGEST,
                    "template_version": REFERENCE_FIXTURE_VERSION,
                }:
                    raise Refused("setup response differs from the reserved identity")
                return response.status_code


def prepare(
    database_url: str,
    application_database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    setup_token: str,
    reset_values: dict[str, str],
    observer_config: dict[str, str],
) -> dict[str, Any]:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.geturl() != origin
        or not setup_token.strip()
    ):
        raise Refused("exact loopback setup origin and local credential required")
    arguments: dict[str, Any] = dict(
        workspace_id=str(uuid.UUID(workspace_id)),
        run_id=str(uuid.UUID(run_id)),
        origin=origin,
        reset_credential_ref=reset_credential_ref,
        observer_credential_ref=observer_credential_ref,
        reset_values=reset_values,
        observer_config=observer_config,
    )
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        context = _context(conn, **arguments, create=True)
        fingerprint = digest(context)
        conn.execute(
            "INSERT INTO fixture_setup_reservation(run_id,workspace_id,context,context_digest) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING",
            (run_id, workspace_id, Jsonb(context), fingerprint),
        )
        reservation = conn.execute(
            "SELECT * FROM fixture_setup_reservation WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        assert reservation is not None
        if reservation["context"] != context or reservation["context_digest"] != fingerprint:
            raise Refused("a different setup reservation already owns this run")
        if reservation["observation"] is not None:
            if digest(reservation["observation"]) != reservation["observation_digest"]:
                raise Refused("retained setup observation differs")
            return dict(reservation["observation"])
        prepared_at = reservation["created_at"]
    http_status = _provision(context, setup_token)
    measured = ApplicationObserver(application_database_url).inspect_fixture(
        fixture_nonce=context["nonce"]
    )
    if (
        measured["nonce"] != context["nonce"]
        or measured["templateDigest"] != REFERENCE_FIXTURE_DIGEST
        or measured["variant"] != context["variant"]
        or measured["effectCount"] != 0
        or parse_rfc3339_utc(measured["createdAt"]) < prepared_at
        or parse_rfc3339_utc(measured["createdAt"]) > parse_rfc3339_utc(measured["observedAt"])
    ):
        raise Refused("independent application state is not the fresh empty reserved fixture")
    observation = {
        "meaning": "INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION",
        "contextDigest": fingerprint,
        "setupHttpStatus": http_status,
        "application": measured,
        "reservedAt": to_rfc3339_utc(prepared_at),
    }
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        if _context(conn, **arguments, create=False) != context:
            raise Refused("execution identity changed during setup")
        row = conn.execute(
            "SELECT observation,observation_digest FROM fixture_setup_reservation WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise Refused("setup reservation unavailable")
        if row["observation"] is not None:
            if digest(row["observation"]) != row["observation_digest"]:
                raise Refused("retained setup observation differs")
            return dict(row["observation"])
        conn.execute(
            "UPDATE fixture_setup_reservation SET observation=%s,observation_digest=%s,"
            "observed_at=clock_timestamp() WHERE run_id=%s",
            (Jsonb(observation), digest(observation), run_id),
        )
    return observation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--reset-credential-ref", required=True)
    parser.add_argument("--observer-credential-ref", required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.configuration.open() as source:
            config = json.load(source)
        result = prepare(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            os.environ["ACCESSFORGE_OBSERVER_DATABASE_URL"],
            workspace_id=args.workspace_id,
            run_id=args.run_id,
            origin=args.origin,
            reset_credential_ref=args.reset_credential_ref,
            observer_credential_ref=args.observer_credential_ref,
            setup_token=os.environ["ACCESSFORGE_SETUP_TOKEN"],
            reset_values=config["resetValues"],
            observer_config=config["observerConfig"],
        )
    except Exception:
        parser.exit(
            1, "Fixture setup not confirmed; inspect the retained reservation before retry.\n"
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
