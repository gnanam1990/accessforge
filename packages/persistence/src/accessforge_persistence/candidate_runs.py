"""Trusted candidate preparation and exact endpoint/seal/first-desktop-lease binding."""

from __future__ import annotations

import json
import uuid
from dataclasses import fields
from typing import Any

import psycopg

from accessforge_domain.origins import normalize_origin
from accessforge_domain.states import TERMINAL_STATUSES, RunStatus
from accessforge_domain.timestamps import to_rfc3339_utc

from . import candidate_builds as builds
from . import candidate_endpoints as endpoints
from . import candidate_materializations as materializations
from . import candidate_regressions as regressions
from . import fixtures, patches, projects, runs

Refused = builds.BuildClaimRefused


def _fixture_contract(row: dict[str, Any]) -> str:
    actual = fixtures.contract_digest(
        template_id=row["template_id"],
        template_digest=row["template_digest"],
        navigator_values=row["navigator_values"],
        observer_config=row["observer_config"],
    )
    if row["captured_contract_digest"] != actual:
        raise Refused("fixture has no intact creation-time contract; historical data is not proof")
    return actual


def _environment(conn: psycopg.Connection[dict[str, Any]], identifier: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM environment_manifest WHERE id=%s FOR SHARE", (identifier,)
    ).fetchone()
    if row is None:
        raise Refused("candidate environment is absent or foreign")
    spec = projects.EnvironmentSpec(
        name=row["name"],
        allowed_origins=frozenset(normalize_origin(o) for o in row["allowed_origins"]),
        fixture_reset_strategy=row["fixture_reset_strategy"],
        observer_credential_ref=row["observer_credential_ref"],
        reset_credential_ref=row["reset_credential_ref"],
        permitted_effects=frozenset(row["permitted_effects"]),
        expires_at=to_rfc3339_utc(row["expires_at"]),
    )
    if spec.config_digest() != row["config_digest"]:
        raise Refused("environment fields differ from their recorded digest")
    return row


def _authorized_environment(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    environment_id: str,
    project_id: str,
    workspace_id: str,
    endpoint: dict[str, Any],
) -> dict[str, Any]:
    env = _environment(conn, environment_id)
    if (
        str(env["project_id"]) != project_id
        or str(env["workspace_id"]) != workspace_id
        or env["allowed_origins"] != [endpoint["origin"]]
        or env["expires_at"] > endpoint["expires_at"]
    ):
        raise Refused("environment must bind only this endpoint, project and bounded lifetime")
    member = conn.execute(
        "SELECT 1 FROM workspace_membership m JOIN app_user u ON u.id=m.user_id "
        "WHERE m.workspace_id=%s AND m.user_id=%s AND m.revoked_at IS NULL "
        "AND u.disabled_at IS NULL",
        (workspace_id, env["authorized_by"]),
    ).fetchone()
    if member is None:
        raise Refused("environment has no current named workspace authorizer")
    try:
        projects.assert_environment_usable(
            conn, environment_id=environment_id, now=to_rfc3339_utc(builds._moment(conn, None))
        )
    except projects.ProjectError as exc:
        raise Refused(str(exc)) from exc
    return env


def prepare(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    environment_id: str,
    observed_artifact_digest: str,
    observed_fixture_digest: str,
) -> dict[str, Any]:
    """Use separate environment authorization; patch approval never implies reader actions."""
    with conn.transaction():
        parent = regressions._owned(conn, claim, "DISPATCHED")
        workspace = str(parent["workspace_id"])
        build = regressions._authority(conn, claim.build_id, workspace)
        endpoints.assert_live(conn, claim=claim)
        endpoint = endpoints._record(conn, claim)
        if conn.execute(
            "SELECT 1 FROM candidate_run_binding WHERE regression_attempt_id=%s",
            (claim.attempt_id,),
        ).fetchone():
            raise Refused("candidate run already prepared; never implicitly replace it")
        verification = conn.execute(
            "SELECT * FROM patch_verification WHERE id=%s FOR UPDATE", (build["verification_id"],)
        ).fetchone()
        if (
            verification is None
            or verification["state"] != "BUILDING"
            or verification["candidate_run_id"] is not None
        ):
            raise Refused("verification already has a candidate or conclusion")
        baseline = builds._baseline(conn, str(build["patch_id"]), workspace)
        baseline_id = str(verification["baseline_run_id"])
        baseline_run = conn.execute("SELECT * FROM run WHERE id=%s", (baseline_id,)).fetchone()
        closed, unclosed = patches._producer_watermarks(conn, baseline_id)
        if (
            baseline_id != str(baseline["baseline_run_id"])
            or baseline_run is None
            or baseline_run["status"] != "COMPLETED"
            or baseline_run["outcome"] != "FAIL"
            or not closed
            or unclosed
        ):
            raise Refused(
                "candidate requires the exact completed failed baseline with closed producers"
            )
        base_env = _environment(conn, str(baseline["environment_manifest_id"]))
        env = _authorized_environment(
            conn,
            environment_id=environment_id,
            project_id=str(build["project_id"]),
            workspace_id=workspace,
            endpoint=endpoint,
        )
        if base_env["config_digest"] != baseline["environment_config_digest"]:
            raise Refused("baseline environment changed after sealing")
        for field in (
            "name",
            "fixture_reset_strategy",
            "observer_credential_ref",
            "reset_credential_ref",
            "permitted_effects",
        ):
            if env[field] != base_env[field]:
                raise Refused("candidate environment changed a frozen field: " + field)
        fixture = conn.execute(
            "SELECT * FROM run_fixture_instance WHERE run_id=%s FOR SHARE", (baseline_id,)
        ).fetchone()
        nonce = str(endpoint["plan"]["path"]).removeprefix("/form/")
        if fixture is None or fixture["template_digest"] != observed_fixture_digest:
            raise Refused("baseline fixture definition does not match the observed candidate")
        fixture_contract = _fixture_contract(fixture)
        if conn.execute("SELECT 1 FROM run_fixture_instance WHERE nonce=%s", (nonce,)).fetchone():
            raise Refused("candidate fixture was already bound to a run")
        materialized = materializations.publish(
            conn,
            workspace_id=workspace,
            build_id=claim.build_id,
            observed_artifact_digest=observed_artifact_digest,
        )
        run_id = str(uuid.uuid4())
        seal = projects.seal_run(
            conn,
            workspace_id=workspace,
            project_id=str(build["project_id"]),
            run_id=run_id,
            source_snapshot_id=str(materialized["source_snapshot_id"]),
            build_artifact_id=str(materialized["build_artifact_id"]),
            environment_manifest_id=environment_id,
            inputs=projects.SealInputs(
                **{f.name: baseline[f.name] for f in fields(projects.SealInputs)}
            ),
        )
        runs.create_run(
            conn,
            workspace_id=workspace,
            project_id=str(build["project_id"]),
            run_id=run_id,
            manifest_digest=seal.manifest_digest,
        )
        conn.execute(
            "INSERT INTO run_fixture_instance(id,workspace_id,run_id,template_id,template_digest,"
            "nonce,navigator_values,observer_config,captured_contract_digest) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)",
            (
                str(uuid.uuid4()),
                workspace,
                run_id,
                fixture["template_id"],
                observed_fixture_digest,
                nonce,
                json.dumps(fixture["navigator_values"]),
                json.dumps(fixture["observer_config"]),
                fixture_contract,
            ),
        )
        differences = [
            {
                "field": "environment_config_digest",
                "baseline": base_env["config_digest"],
                "candidate": env["config_digest"],
                "reason": "exact authorized isolated candidate origin",
            }
        ]
        binding = conn.execute(
            "INSERT INTO candidate_run_binding(run_id,workspace_id,regression_attempt_id,build_id,"
            "verification_id,baseline_run_id,sealed_manifest_id,fixture_nonce,fixture_template_digest,"
            "endpoint_binding_digest,permitted_differences,fixture_contract_digest) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *",
            (
                run_id,
                workspace,
                claim.attempt_id,
                claim.build_id,
                build["verification_id"],
                baseline_id,
                seal.sealed_manifest_id,
                nonce,
                observed_fixture_digest,
                endpoint["binding_digest"],
                json.dumps(differences),
                fixture_contract,
            ),
        ).fetchone()
        conn.execute(
            "UPDATE patch_verification SET candidate_run_id=%s WHERE id=%s",
            (run_id, build["verification_id"]),
        )
        endpoints.assert_live(conn, claim=claim)
        _authorized_environment(
            conn,
            environment_id=environment_id,
            project_id=str(build["project_id"]),
            workspace_id=workspace,
            endpoint=endpoint,
        )
        assert binding is not None
        return binding


def assert_live(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> dict[str, Any] | None:
    """Non-candidate runs are unchanged. Candidate runs must retain their exact live binding."""
    binding = conn.execute(
        "SELECT * FROM candidate_run_binding WHERE run_id=%s", (run_id,)
    ).fetchone()
    if binding is None:
        return None
    parent = conn.execute(
        "SELECT * FROM candidate_regression_attempt WHERE id=%s",
        (binding["regression_attempt_id"],),
    ).fetchone()
    if parent is None:
        raise Refused("candidate regression provenance is absent")
    claim = regressions.RegressionClaim(
        str(parent["id"]),
        str(parent["build_id"]),
        str(parent["worker_token"]),
        int(parent["epoch"]),
    )
    endpoints.assert_live(conn, claim=claim)
    endpoint = endpoints._record(conn, claim)
    materialized = materializations.publish(
        conn,
        workspace_id=str(binding["workspace_id"]),
        build_id=str(binding["build_id"]),
        observed_artifact_digest=str(parent["artifact_digest"]),
    )
    seal = conn.execute(
        "SELECT * FROM sealed_manifest WHERE id=%s", (binding["sealed_manifest_id"],)
    ).fetchone()
    run = conn.execute("SELECT * FROM run WHERE id=%s", (run_id,)).fetchone()
    if (
        seal is None
        or run is None
        or RunStatus(run["status"]) in TERMINAL_STATUSES
        or run["cancel_requested_at"] is not None
        or run["quarantined"]
        or str(seal["run_id"]) != run_id
        or seal["manifest_digest"] != run["manifest_digest"]
        or seal["project_id"] != run["project_id"]
        or seal["source_snapshot_id"] != materialized["source_snapshot_id"]
        or seal["build_artifact_id"] != materialized["build_artifact_id"]
        or endpoint["binding_digest"] != binding["endpoint_binding_digest"]
    ):
        raise Refused("candidate run no longer matches its exact source/build/endpoint seal")
    fixture = conn.execute(
        "SELECT * FROM run_fixture_instance WHERE run_id=%s", (run_id,)
    ).fetchone()
    if (
        fixture is None
        or fixture["nonce"] != binding["fixture_nonce"]
        or fixture["template_digest"] != binding["fixture_template_digest"]
        or _fixture_contract(fixture) != binding["fixture_contract_digest"]
    ):
        raise Refused("candidate fixture differs from its exact fresh binding")
    env = _authorized_environment(
        conn,
        environment_id=str(seal["environment_manifest_id"]),
        project_id=str(seal["project_id"]),
        workspace_id=str(binding["workspace_id"]),
        endpoint=endpoint,
    )
    if env["config_digest"] != seal["environment_config_digest"]:
        raise Refused("candidate environment changed after sealing")
    endpoints.assert_live(conn, claim=claim)
    return {
        **binding,
        "endpoint_expires_at": endpoint["expires_at"],
        "runner_profile_digest": seal["runner_profile_digest"],
    }


def record_lease(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, lease_id: str, epoch: int
) -> None:
    binding = assert_live(conn, run_id=run_id)
    if binding is None:
        return
    lease = conn.execute(
        "SELECT l.*,r.profile_digest FROM desktop_lease l JOIN runner r ON r.id=l.runner_id "
        "WHERE l.id=%s",
        (lease_id,),
    ).fetchone()
    if (
        lease is None
        or str(lease["run_id"]) != run_id
        or lease["epoch"] != epoch
        or lease["released_at"] is not None
        or lease["deadline_at"] > binding["endpoint_expires_at"]
        or lease["deadline_at"] <= builds._moment(conn, None)
        or lease["profile_digest"] != binding["runner_profile_digest"]
    ):
        raise Refused("candidate reader lease differs from exact profile/run or endpoint lifetime")
    if conn.execute("SELECT 1 FROM candidate_reader_lease WHERE run_id=%s", (run_id,)).fetchone():
        raise Refused("candidate already bound its first reader lease; a retry requires a new run")
    conn.execute(
        "INSERT INTO candidate_reader_lease(run_id,workspace_id,lease_id,lease_epoch) "
        "VALUES (%s,%s,%s,%s)",
        (run_id, binding["workspace_id"], lease_id, epoch),
    )


def assert_lease(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str, lease_id: str, epoch: int
) -> None:
    binding = assert_live(conn, run_id=run_id)
    if binding is None:
        return
    row = conn.execute(
        "SELECT lease_id,lease_epoch FROM candidate_reader_lease WHERE run_id=%s", (run_id,)
    ).fetchone()
    if row != {"lease_id": uuid.UUID(lease_id), "lease_epoch": epoch}:
        raise Refused("candidate dispatch does not hold its originally bound reader lease")
    lease = conn.execute(
        "SELECT l.*,r.profile_digest,r.lease_epoch AS current_epoch,r.revoked_at,"
        "r.quarantined_at FROM desktop_lease l JOIN runner r ON r.id=l.runner_id WHERE l.id=%s",
        (lease_id,),
    ).fetchone()
    if (
        lease is None
        or str(lease["run_id"]) != run_id
        or lease["epoch"] != epoch
        or lease["current_epoch"] != epoch
        or lease["profile_digest"] != binding["runner_profile_digest"]
        or lease["revoked_at"] is not None
        or lease["quarantined_at"] is not None
        or lease["released_at"] is not None
        or lease["cancel_requested_at"] is not None
        or lease["deadline_at"] > binding["endpoint_expires_at"]
        or lease["deadline_at"] <= builds._moment(conn, None)
    ):
        raise Refused("candidate reader lease is expired, released or foreign")


def assert_request(
    conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str, method: str
) -> None:
    """Preview has no run; sealed sessions cannot outlive their first reader lease.

    GET before lease admission is trusted setup only. Effects stay closed until the canonical
    controller supplies independently checked RUN_EFFECTS authority, not merely a live lease.
    """
    row = conn.execute(
        "SELECT b.run_id,l.lease_id,l.lease_epoch FROM candidate_run_binding b "
        "LEFT JOIN candidate_reader_lease l USING(run_id,workspace_id) "
        "WHERE b.regression_attempt_id=%s",
        (attempt_id,),
    ).fetchone()
    if row is None:
        return
    run_id = str(row["run_id"])
    if row["lease_id"] is None:
        assert_live(conn, run_id=run_id)
    else:
        assert_lease(conn, run_id=run_id, lease_id=str(row["lease_id"]), epoch=row["lease_epoch"])
    if method != "GET":
        raise Refused("canonical RUN_EFFECTS transport authorization is not yet available")


def assert_reader_released(conn: psycopg.Connection[dict[str, Any]], *, attempt_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM candidate_run_binding b JOIN candidate_reader_lease c "
        "USING(run_id,workspace_id) JOIN desktop_lease l ON l.id=c.lease_id "
        "WHERE b.regression_attempt_id=%s AND l.released_at IS NULL",
        (attempt_id,),
    ).fetchone()
    if row is not None:
        raise Refused("candidate reader lease remains unresolved; expiry is not a stop")
