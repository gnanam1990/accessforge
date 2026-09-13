"""Exact short-lived candidate form-effect permission; never an execution/effect receipt."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import candidate_observations, supervisor_sessions

Refused = supervisor_sessions.Refused


def authorize_form(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    session_id: str,
    action_id: str,
    token: str,
) -> dict[str, Any]:
    """Called after the trusted desktop's physical guards, before its adapter invocation.

    No effect is performed here. The candidate HTTP transport stays closed until it can commit
    consumption of this original permit and revalidate current authority before the single POST.
    """
    session, manifest = supervisor_sessions._live(conn, workspace_id, session_id, token)
    action = supervisor_sessions._action(conn, session, action_id)
    if (
        action["dispatched_at"] is None
        or not (
            action["action"] == "ACTIVATE"
            or (action["action"] == "KEY_CHORD" and action.get("key_chord") in {"ENTER", "SPACE"})
        )
        or "FORM_SUBMIT" not in manifest["permittedEffects"]
    ):
        raise Refused("original form-effect action authority required")
    binding = conn.execute(
        "SELECT b.*,e.expires_at,e.plan FROM candidate_run_binding b JOIN candidate_endpoint e "
        "ON e.attempt_id=b.regression_attempt_id WHERE b.run_id=%s",
        (session["run_id"],),
    ).fetchone()
    if binding is None:
        raise Refused("form effects require an original candidate deployment binding")
    prior = conn.execute(
        "SELECT * FROM candidate_action_effect_permit WHERE action_id=%s",
        (action_id,),
    ).fetchone()
    if prior is not None:
        return _view(prior)  # Original expiry, never another five seconds or another permit.
    event = conn.execute(
        "SELECT e.* FROM producer_source_record p JOIN canonical_event e ON e.event_id=p.event_id "
        "WHERE p.attempt_id=%s AND p.producer_id=%s AND p.source_record_id=%s",
        (
            session["attempt_id"],
            f"supervisor:{session_id}:lifecycle",
            "runtime-preflight:" + action_id,
        ),
    ).fetchone()
    if event is None:
        raise Refused("retained runtime preflight required before effect permission")
    payload = event["payload"]
    if not isinstance(payload, dict):
        raise Refused("original runtime preflight payload unavailable")
    source = payload.get("sourceRecord", {})
    if not isinstance(source, dict):
        raise Refused("original runtime preflight source unavailable")
    checks = source.get("checks", {})
    if not isinstance(checks, dict):
        raise Refused("original runtime preflight checks unavailable")
    if (
        event["event_type"] != "PREFLIGHT_RESULT"
        or event["payload_digest"] != digest(payload)
        or event["manifest_digest"] != digest(manifest)
        or event["lease_epoch"] != session["epoch"]
        or payload.get("serviceIdentity") != "SUPERVISOR"
        or payload.get("provenance") != "RUNTIME_PROBE_REPORT"
        or payload.get("sourceRecordDigest") != digest(source)
        or source.get("actionId") != action_id
        or source.get("actionSequence") != action["action_sequence"]
        or set(checks) != set(REQUIRED_PREFLIGHT_CHECKS)
        or any(value != "TRUE" for value in checks.values())
    ):
        raise Refused("complete matching runtime preflight required before form effects")
    try:
        captured = parse_rfc3339_utc(source["capturedAtUtc"])
        measurement = candidate_observations.for_runtime_preflight(
            conn,
            session=session,
            dispatched_at=action["dispatched_at"],
            captured_at=captured,
            manifest=manifest,
        )
    except (KeyError, TypeError, ValueError, candidate_observations.Refused) as exc:
        raise Refused("current measured build authority unavailable") from exc
    if measurement is None or measurement != payload.get("buildArtifactReceipt"):
        raise Refused("original action-bound build measurement required")
    bounds = conn.execute(
        "SELECT clock_timestamp() AS now,s.expires_at AS session_expiry,l.deadline_at,"
        "a.expires_at AS approval_expiry "
        "FROM supervisor_execution_session s JOIN desktop_lease l ON l.id=%s "
        "JOIN run r ON r.id=l.run_id JOIN approval a ON a.id=r.authorization_id "
        "WHERE s.ticket_id=%s",
        (session["lease_id"], session_id),
    ).fetchone()
    if bounds is None:
        raise Refused("effect permission lifetime unavailable")
    now = bounds["now"]
    expires = min(
        now + timedelta(seconds=5),
        binding["expires_at"],
        bounds["session_expiry"],
        bounds["deadline_at"],
        bounds["approval_expiry"],
        action["dispatched_at"] + timedelta(seconds=15),
    )
    if expires <= now or not now - timedelta(seconds=10) <= captured <= now + timedelta(seconds=5):
        raise Refused("runtime action evidence is stale")
    grant = {
        "workspaceId": workspace_id,
        "sessionId": session_id,
        "actionId": action_id,
        "runId": str(session["run_id"]),
        "attemptId": str(session["attempt_id"]),
        "runnerId": str(session["runner_id"]),
        "leaseId": str(session["lease_id"]),
        "leaseEpoch": session["epoch"],
        "regressionAttemptId": str(binding["regression_attempt_id"]),
        "endpointBindingDigest": binding["endpoint_binding_digest"],
        "manifestDigest": event["manifest_digest"],
        "preflightEventId": str(event["event_id"]),
        "method": "POST",
        "path": binding["plan"]["path"],
        "effect": "FORM_SUBMIT",
        "expiresAt": to_rfc3339_utc(expires),
    }
    fingerprint = digest(grant)
    identifier = str(uuid5(NAMESPACE_URL, "accessforge:action-effect:" + fingerprint))
    stored = conn.execute(
        "INSERT INTO candidate_action_effect_permit "
        "(id,workspace_id,action_id,regression_attempt_id,preflight_event_id,grant_digest,"
        "grant_payload,granted_at,expires_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            identifier,
            workspace_id,
            action_id,
            binding["regression_attempt_id"],
            event["event_id"],
            fingerprint,
            Jsonb(grant),
            now,
            expires,
        ),
    ).fetchone()
    assert stored is not None
    return _view(stored)


def _view(row: dict[str, Any]) -> dict[str, Any]:
    grant = row["grant_payload"]
    if not isinstance(grant, dict) or not {"sessionId", "expiresAt"} <= set(grant):
        raise Refused("original effect permit payload unavailable")
    fingerprint = digest(grant)
    if fingerprint != row["grant_digest"] or str(row["id"]) != str(
        uuid5(NAMESPACE_URL, "accessforge:action-effect:" + fingerprint)
    ):
        raise Refused("original effect permit integrity unavailable")
    if any(
        grant.get(key) != str(row[column])
        for key, column in {
            "workspaceId": "workspace_id",
            "actionId": "action_id",
            "regressionAttemptId": "regression_attempt_id",
            "preflightEventId": "preflight_event_id",
        }.items()
    ) or grant["expiresAt"] != to_rfc3339_utc(row["expires_at"]):
        raise Refused("original effect permit binding differs")
    return {
        "sessionId": grant["sessionId"],
        "actionId": grant["actionId"],
        "permitId": str(row["id"]),
        "expiresAt": grant["expiresAt"],
        "meaning": "ACTION_FORM_PERMISSION_NOT_EFFECT_PROOF",
    }
