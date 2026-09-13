"""Explicit navigator model consent and durable one-shot budget admission.

These functions never invoke a provider or accept model-supplied authority. A trusted coordinator
must validate the complete reader-only projection, reserve in its own committed transaction, then
recheck live consent/attempt authority before invoking. A persisted reservation is never resumable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.authorization.roles import Permission, Role, permissions_for
from accessforge_domain.canonical import digest
from accessforge_domain.navigator_model import default_profile, reserved_tokens, validate_profile
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import diagnosis_invocations, execution_approvals, projects, runners


class Refused(ValueError):
    """No fresh consent/turn authority; never interpret this as permission to retry a call."""


def _actor(conn: psycopg.Connection[Any], workspace_id: str, actor_id: str) -> None:
    row = conn.execute(
        "SELECT role FROM workspace_membership WHERE workspace_id=%s AND user_id=%s FOR SHARE",
        (workspace_id, actor_id),
    ).fetchone()
    if row is None or Permission.RUN_APPROVE not in permissions_for(Role(row["role"])):
        raise Refused("current model-consent authority unavailable")


def _fingerprint(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise Refused("exact canonical digest required")


def review_scope(
    conn: psycopg.Connection[Any], *, workspace_id: str, run_id: str
) -> dict[str, Any]:
    run = conn.execute(
        "SELECT revision,manifest_digest FROM run WHERE id=%s AND workspace_id=%s",
        (run_id, workspace_id),
    ).fetchone()
    if run is None:
        raise LookupError("run unavailable")
    sealed = projects.find_sealed_manifest(conn, manifest_digest=str(run["manifest_digest"]))
    if sealed is None:
        raise Refused("canonical execution seal required")
    manifest = execution_approvals.assert_authorized(
        conn, sealed_manifest_id=sealed.sealed_manifest_id, run_id=run_id, workspace_id=workspace_id
    )
    profile = default_profile()
    if digest(profile) != manifest["modelConfigDigest"]:
        raise Refused(
            "seal does not match the default profile; review its exact configured profile"
        )
    approval = conn.execute(
        "SELECT expires_at FROM approval WHERE id=%s", (manifest["authorizationId"],)
    ).fetchone()
    if approval is None:
        raise Refused("execution approval unavailable")
    return {
        "revision": run["revision"],
        "manifestDigest": run["manifest_digest"],
        "modelConfigDigest": manifest["modelConfigDigest"],
        "modelProfile": profile,
        "maximumCalls": manifest["actionBudget"],
        "tokensPerCall": reserved_tokens(profile),
        "maximumExpiresAt": to_rfc3339_utc(
            min(approval["expires_at"], parse_rfc3339_utc(manifest["expiresAt"]))
        ),
        "billableCallAcknowledged": False,
        "disclosure": "Task intent, safe fixture values and retained reader announcements are "
        "sent to this provider. Model calls and configured retries may be billable. "
        "Token reservations are not a currency spending cap.",
        "meaning": "PREVIEW_NOT_MODEL_CONSENT_OR_INVOCATION",
    }


def inspect_consent(conn: psycopg.Connection[Any], *, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM navigator_model_consent WHERE run_id=%s", (run_id,)
    ).fetchone()
    if row is None:
        raise LookupError("navigator model consent unavailable")
    validate_profile(row["model_profile"])
    if (
        digest(row["model_profile"]) != row["model_config_digest"]
        or reserved_tokens(row["model_profile"]) != row["tokens_per_call"]
    ):
        raise Refused("retained navigator consent profile changed")
    calls = conn.execute(
        "SELECT t.operation_id,t.action_sequence,i.status,i.reserved_tokens,i.created_at,"
        "i.finished_at FROM navigator_model_turn t JOIN diagnosis_invocation i "
        "ON i.operation_id=t.operation_id AND i.workspace_id=t.workspace_id "
        "WHERE t.consent_id=%s AND i.purpose='NAVIGATOR' ORDER BY t.action_sequence LIMIT 501",
        (row["id"],),
    ).fetchall()
    return {
        "consentId": str(row["id"]),
        "runId": str(row["run_id"]),
        "actorId": str(row["actor_user"]),
        "manifestDigest": row["manifest_digest"],
        "modelConfigDigest": row["model_config_digest"],
        "modelProfile": row["model_profile"],
        "maxCalls": row["max_calls"],
        "tokensPerCall": row["tokens_per_call"],
        "expiresAt": to_rfc3339_utc(row["expires_at"]),
        "revokedAt": None if row["revoked_at"] is None else to_rfc3339_utc(row["revoked_at"]),
        "invocations": [
            {
                "operationId": str(call["operation_id"]),
                "afterActionSequence": call["action_sequence"],
                "status": call["status"],
                "reservedTokens": call["reserved_tokens"],
                "createdAt": to_rfc3339_utc(call["created_at"]),
                "finishedAt": None
                if call["finished_at"] is None
                else to_rfc3339_utc(call["finished_at"]),
            }
            for call in calls
        ],
        "meaning": "STORED_MODEL_CONSENT_NOT_INVOCATION_OR_FINANCIAL_CAP",
        "disclosure": "Approved task intent, safe fixture values and retained reader announcements "
        "may be disclosed to this provider. Calls and configured retries may be billable. "
        "Token reservations are not measured usage or a currency spending cap.",
    }


def issue_consent(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    consent_id: str,
    actor_id: str,
    expected_revision: int,
    manifest_digest: str,
    model_profile: dict[str, Any],
    max_calls: int,
    expires_at: str,
    billable_call_acknowledged: bool,
) -> dict[str, Any]:
    """One immutable grant per run; requester must be authenticated by the API/operator boundary."""
    validate_profile(model_profile)
    _fingerprint(manifest_digest)
    if str(UUID(consent_id)) != consent_id or billable_call_acknowledged is not True:
        raise Refused("canonical operation identity and explicit billable acknowledgement required")
    run = conn.execute(
        "SELECT * FROM run WHERE id=%s AND workspace_id=%s FOR UPDATE", (run_id, workspace_id)
    ).fetchone()
    _actor(conn, workspace_id, actor_id)
    if (
        run is None
        or run["status"] not in {"QUEUED", "LEASED", "RUNNING"}
        or run["cancel_requested_at"] is not None
        or run["quarantined"]
        or type(expected_revision) is not int
        or expected_revision != run["revision"]
        or run["manifest_digest"] != manifest_digest
        or type(max_calls) is not int
        or not 1 <= max_calls <= 500
    ):
        raise Refused("exact reviewed active run required")
    seal = projects.find_sealed_manifest(conn, manifest_digest=manifest_digest)
    if seal is None:
        raise Refused("canonical execution seal required")
    manifest = execution_approvals.assert_authorized(
        conn, sealed_manifest_id=seal.sealed_manifest_id, run_id=run_id, workspace_id=workspace_id
    )
    expires = parse_rfc3339_utc(expires_at)
    approval = conn.execute(
        "SELECT expires_at FROM approval WHERE id=%s FOR SHARE", (manifest["authorizationId"],)
    ).fetchone()
    if (
        digest(model_profile) != manifest["modelConfigDigest"]
        or max_calls > manifest["actionBudget"]
        or approval is None
        or not datetime.now(UTC)
        < expires
        <= min(approval["expires_at"], parse_rfc3339_utc(manifest["expiresAt"]))
    ):
        raise Refused("model profile, call count or expiry differs from the approved execution")
    if conn.execute("SELECT 1 FROM navigator_model_consent WHERE run_id=%s", (run_id,)).fetchone():
        raise Refused("consent already exists; inspect it rather than issue another grant")
    conn.execute(
        "INSERT INTO navigator_model_consent(id,workspace_id,run_id,actor_user,manifest_digest,"
        "model_config_digest,model_profile,max_calls,tokens_per_call,billable_call_acknowledged,"
        "expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s)",
        (
            consent_id,
            workspace_id,
            run_id,
            actor_id,
            manifest_digest,
            digest(model_profile),
            Jsonb(model_profile),
            max_calls,
            reserved_tokens(model_profile),
            expires,
        ),
    )
    conn.execute(
        "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,outcome) "
        "VALUES(%s,%s,'NAVIGATOR_MODEL_CONSENT_ISSUED','run',%s,'ALLOWED')",
        (workspace_id, actor_id, run_id),
    )
    return inspect_consent(conn, run_id=run_id)


def revoke_consent(
    conn: psycopg.Connection[Any], *, workspace_id: str, run_id: str, actor_id: str, consent_id: str
) -> dict[str, Any]:
    # Same run -> consent ordering as admission. This is not proof an in-flight call stopped.
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (run_id,))
    _actor(conn, workspace_id, actor_id)
    current = inspect_consent(conn, run_id=run_id)
    if current["consentId"] != consent_id:
        raise Refused("exact consent identity required for revocation")
    row = conn.execute(
        "UPDATE navigator_model_consent SET revoked_at=clock_timestamp() "
        "WHERE run_id=%s AND workspace_id=%s AND revoked_at IS NULL RETURNING id",
        (run_id, workspace_id),
    ).fetchone()
    if row is not None:
        conn.execute(
            "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,outcome) "
            "VALUES(%s,%s,'NAVIGATOR_MODEL_CONSENT_REVOKED','run',%s,'ALLOWED')",
            (workspace_id, actor_id, run_id),
        )
    return inspect_consent(conn, run_id=run_id)


def _live(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    runner_id: str,
    lease_id: str,
    epoch: int,
    consent_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (runner_id,))
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (run_id,))
    conn.execute("SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE", (lease_id,))
    conn.execute(
        "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
        "WHERE r.id=%s FOR SHARE OF a",
        (run_id,),
    )
    manifest = runners.assert_manual_attempt_authorized(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        runner_id=runner_id,
        lease_id=lease_id,
        epoch=epoch,
    )
    consent = conn.execute(
        "SELECT * FROM navigator_model_consent WHERE id=%s AND workspace_id=%s AND run_id=%s "
        "AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE",
        (consent_id, workspace_id, run_id),
    ).fetchone()
    session = conn.execute(
        "SELECT t.id,t.created_at,s.expires_at FROM supervisor_dispatch_ticket t "
        "JOIN supervisor_execution_session s ON s.ticket_id=t.id AND s.workspace_id=t.workspace_id "
        "WHERE t.workspace_id=%s AND t.run_id=%s AND t.attempt_id=%s AND t.runner_id=%s "
        "AND t.lease_id=%s AND t.epoch=%s AND t.accepted_at IS NOT NULL AND t.revoked_at IS NULL "
        "AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() FOR SHARE OF t,s",
        (workspace_id, run_id, attempt_id, runner_id, lease_id, epoch),
    ).fetchone()
    if consent is None or session is None:
        raise Refused("current consent and exact live supervisor session required")
    _actor(conn, workspace_id, str(consent["actor_user"]))
    if consent["manifest_digest"] != digest(manifest) or (
        consent["model_config_digest"] != manifest["modelConfigDigest"]
    ):
        raise Refused("consent is for a different execution or provider configuration")
    inspect_consent(conn, run_id=run_id)
    policy = conn.execute(
        "SELECT navigator_policy FROM journey_version WHERE id=%s", (manifest["journeyVersionId"],)
    ).fetchone()
    if policy is None or digest(policy["navigator_policy"]) != manifest["navigatorPolicyDigest"]:
        raise Refused("exact navigator policy unavailable")
    wall = policy["navigator_policy"].get("wallTimeSeconds")
    if (
        type(wall) is not int
        or wall <= 0
        or datetime.now(UTC)
        >= session["created_at"] + timedelta(seconds=min(wall, manifest["wallTimeBudgetSeconds"]))
    ):
        raise Refused("navigator wall-time budget expired")
    maximum = policy["navigator_policy"].get("maxActions")
    if type(maximum) is not int or not 1 <= maximum <= 500:
        raise Refused("bounded navigator action policy required")
    return (
        dict(consent),
        manifest,
        {**dict(session), "planning_action_budget": min(maximum, manifest["actionBudget"])},
    )


def reserve_turn(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    runner_id: str,
    lease_id: str,
    epoch: int,
    consent_id: str,
    operation_id: str,
    model_config_digest: str,
    projection_digest: str,
    action_sequence: int,
    reader_records: tuple[tuple[str, str], ...],
) -> str:
    """Reserve exactly once, in the caller's transaction, BEFORE any provider construction/call.

    Reader pairs are original (event ID, payload digest) from the validated projection loader.
    They are re-read under action admission locks so a stale prepared turn cannot reserve a call.
    Neither a new operation ID nor another process can bypass a consumed action boundary.
    """
    _fingerprint(projection_digest)
    _fingerprint(model_config_digest)
    if (
        str(UUID(operation_id)) != operation_id
        or type(action_sequence) is not int
        or not (0 <= action_sequence < 500 and len(reader_records) == action_sequence)
    ):
        raise Refused("canonical invocation and bounded reader boundary required")
    consent, manifest, session = _live(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        runner_id=runner_id,
        lease_id=lease_id,
        epoch=epoch,
        consent_id=consent_id,
    )
    if model_config_digest != consent["model_config_digest"]:
        raise Refused("configured model differs from reviewed consent")
    prior = conn.execute(
        "SELECT t.action_sequence,i.status FROM navigator_model_turn t "
        "JOIN diagnosis_invocation i ON i.operation_id=t.operation_id "
        "AND i.workspace_id=t.workspace_id "
        "WHERE t.run_id=%s",
        (run_id,),
    ).fetchall()
    if len(prior) >= consent["max_calls"] or any(
        row["action_sequence"] >= action_sequence or row["status"] != "RECORDED" for row in prior
    ):
        raise Refused("call limit, consumed boundary or unresolved prior invocation; never retry")
    actions = conn.execute(
        "SELECT action_sequence,action,result_status,result_at,dispatched_at FROM runner_action "
        "WHERE run_id=%s AND attempt_id=%s ORDER BY action_sequence LIMIT 501",
        (run_id, attempt_id),
    ).fetchall()
    if (
        len(actions) != action_sequence
        or action_sequence >= session["planning_action_budget"]
        or any(
            row["action_sequence"] != index
            or row["action"] == "STOP"
            or row["result_status"] not in {"SUCCEEDED", "FAILED"}
            or row["result_at"] is None
            or row["dispatched_at"] is None
            for index, row in enumerate(actions, start=1)
        )
    ):
        raise Refused("prepared projection is no longer the resolved action boundary")
    producer = f"supervisor:{session['id']}:reader"
    stream = conn.execute(
        "SELECT admitted_through,closed_at_sequence FROM producer_stream "
        "WHERE attempt_id=%s AND producer_id=%s",
        (attempt_id, producer),
    ).fetchone()
    records = conn.execute(
        "SELECT e.event_id,e.payload_digest,e.payload FROM producer_source_record p "
        "JOIN canonical_event e ON e.event_id=p.event_id AND e.workspace_id=p.workspace_id "
        "AND e.run_id=p.run_id AND e.attempt_id=p.attempt_id "
        "WHERE p.run_id=%s AND p.attempt_id=%s AND p.producer_id=%s "
        "ORDER BY p.producer_sequence LIMIT 501",
        (run_id, attempt_id, producer),
    ).fetchall()
    if (
        stream is None
        or stream["admitted_through"] != action_sequence
        or stream["closed_at_sequence"] is not None
        or tuple((str(row["event_id"]), row["payload_digest"]) for row in records) != reader_records
        or any(digest(row["payload"]) != row["payload_digest"] for row in records)
    ):
        raise Refused("original projection reader records changed before reservation")
    reader_digest = digest([list(pair) for pair in reader_records])
    request_digest = digest(
        {
            "workspaceId": workspace_id,
            "runId": run_id,
            "attemptId": attempt_id,
            "runnerId": runner_id,
            "leaseId": lease_id,
            "epoch": epoch,
            "consentId": consent_id,
            "actionSequence": action_sequence,
            "projectionDigest": projection_digest,
            "readerRecordsDigest": reader_digest,
            "modelConfigDigest": model_config_digest,
        }
    )
    diagnosis_invocations.reserve(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        operation_id=operation_id,
        request_digest=request_digest,
        tokens=consent["tokens_per_call"],
        purpose="NAVIGATOR",
    )
    conn.execute(
        "INSERT INTO navigator_model_turn(operation_id,workspace_id,run_id,attempt_id,runner_id,"
        "lease_id,lease_epoch,consent_id,action_sequence,projection_digest,reader_records_digest) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            operation_id,
            workspace_id,
            run_id,
            attempt_id,
            runner_id,
            lease_id,
            epoch,
            consent_id,
            action_sequence,
            projection_digest,
            reader_digest,
        ),
    )
    return request_digest


def assert_turn_authorized(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    runner_id: str,
    lease_id: str,
    epoch: int,
    consent_id: str,
    operation_id: str,
    request_digest: str,
    model_config_digest: str,
    projection_digest: str,
    action_sequence: int,
) -> None:
    """Recheck an already committed, in-process turn; never recreate or resume it.

    The coordinator additionally reloads the original reader projection. This function binds that
    read to this exact open reservation and current consent, before construction and dispatch.
    """
    consent, _, _ = _live(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        runner_id=runner_id,
        lease_id=lease_id,
        epoch=epoch,
        consent_id=consent_id,
    )
    row = conn.execute(
        "SELECT 1 FROM navigator_model_turn t JOIN diagnosis_invocation i "
        "ON i.operation_id=t.operation_id AND i.workspace_id=t.workspace_id "
        "WHERE t.workspace_id=%s AND t.operation_id=%s AND t.run_id=%s "
        "AND t.attempt_id=%s AND t.runner_id=%s AND t.lease_id=%s AND t.lease_epoch=%s "
        "AND t.consent_id=%s AND t.action_sequence=%s AND t.projection_digest=%s "
        "AND i.request_digest=%s AND i.purpose='NAVIGATOR' AND i.status='STARTED' "
        "FOR SHARE OF i",
        (
            workspace_id,
            operation_id,
            run_id,
            attempt_id,
            runner_id,
            lease_id,
            epoch,
            consent_id,
            action_sequence,
            projection_digest,
            request_digest,
        ),
    ).fetchone()
    if row is None or model_config_digest != consent["model_config_digest"]:
        raise Refused("original open invocation and exact configured model required")


def finish_turn(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    operation_id: str,
    request_digest: str,
    status: Literal["RECORDED", "UNCONFIRMED", "NOT_CALLED"],
) -> None:
    """Final disposition only. NOT_CALLED requires proof the provider was never entered."""
    if (
        conn.execute(
            "SELECT 1 FROM navigator_model_turn WHERE operation_id=%s AND workspace_id=%s",
            (operation_id, workspace_id),
        ).fetchone()
        is None
    ):
        raise Refused("original navigator reservation unavailable")
    diagnosis_invocations.finish(
        conn,
        workspace_id=workspace_id,
        operation_id=operation_id,
        request_digest=request_digest,
        status=status,
        purpose="NAVIGATOR",
    )
