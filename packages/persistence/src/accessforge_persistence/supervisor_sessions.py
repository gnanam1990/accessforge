"""Exact-attempt machine sessions and durable action-intent admission, not OS execution proof.

The receiver supplies a newly generated secret during one-time bootstrap acceptance. Only its hash
is retained. The bootstrap secret cannot be reused for session calls; restore/handoff revocation of
the parent ticket also invalidates the session. Every intent rechecks current manual authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg

from accessforge_domain import reducers
from accessforge_domain.authorization import (
    MachinePrincipal,
    ServiceIdentity,
    assert_may_submit_event,
)
from accessforge_domain.canonical import digest
from accessforge_domain.journeys.dsl import ALLOWED_ACTIONS, ALLOWED_KEY_CHORDS
from accessforge_domain.origins import normalize_origin
from accessforge_domain.runners.preflight import AmbiguityReason
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import reader_startup_consents, runners, runs, sequencer, supervisor_dispatch
from .evidence import artifacts
from .evidence import session as session_evidence


class Refused(Exception):
    """Machine identity or current exact-attempt authority unavailable."""


def _token_digest(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise Refused("supervisor session unavailable")
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    ticket_id: str,
    bootstrap_token: str,
    session_token: str,
) -> dict[str, Any]:
    """Same transaction consumes the bootstrap and binds the receiver's independent secret."""
    token_digest = _token_digest(session_token)
    if hmac.compare_digest(_token_digest(bootstrap_token), token_digest):
        raise Refused("session must use an independent credential")
    accepted = supervisor_dispatch.accept(
        conn, workspace_id=workspace_id, ticket_id=ticket_id, token=bootstrap_token
    )
    principal = accepted.principal
    bounds = conn.execute(
        "SELECT l.deadline_at,a.expires_at,s.canonical_manifest FROM run r "
        "JOIN desktop_lease l ON l.id=%s JOIN approval a ON a.id=r.authorization_id "
        "JOIN sealed_manifest s ON s.manifest_digest=r.manifest_digest "
        "WHERE r.id=%s AND s.workspace_id=r.workspace_id",
        (principal.lease_id, principal.run_id),
    ).fetchone()
    if bounds is None:
        raise Refused("supervisor session unavailable")
    expires = min(
        bounds["deadline_at"],
        bounds["expires_at"],
        parse_rfc3339_utc(bounds["canonical_manifest"]["expiresAt"], field="expiresAt"),
    )
    if expires <= datetime.now(UTC):
        raise Refused("supervisor session unavailable")
    conn.execute(
        "INSERT INTO supervisor_execution_session(ticket_id,workspace_id,token_digest,expires_at) "
        "VALUES(%s,%s,%s,%s)",
        (ticket_id, workspace_id, token_digest, expires),
    )
    row, _ = _live(conn, workspace_id, ticket_id, session_token)
    session_evidence.start(conn, row)
    return {
        "sessionId": ticket_id,
        "workspaceId": workspace_id,
        "runId": principal.run_id,
        "attemptId": accepted.attempt_id,
        "runnerId": accepted.runner_id,
        "leaseId": principal.lease_id,
        "epoch": accepted.epoch,
        "expiresAt": to_rfc3339_utc(expires),
        "meaning": "SUPERVISOR_SESSION_OPENED",
    }


def _live(
    conn: psycopg.Connection[Any], workspace_id: str, session_id: str, token: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    token_digest = _token_digest(token)
    row = conn.execute(
        "SELECT t.*,s.token_digest AS session_digest FROM supervisor_execution_session s "
        "JOIN supervisor_dispatch_ticket t ON t.id=s.ticket_id AND t.workspace_id=s.workspace_id "
        "WHERE s.ticket_id=%s AND s.workspace_id=%s",
        (session_id, workspace_id),
    ).fetchone()
    if row is None or not hmac.compare_digest(row["session_digest"], token_digest):
        raise Refused("supervisor session unavailable")
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (row["runner_id"],))
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (row["run_id"],))
    conn.execute("SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE", (row["lease_id"],))
    conn.execute(
        "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
        "WHERE r.id=%s FOR SHARE OF a",
        (row["run_id"],),
    )
    current = conn.execute(
        "SELECT 1 FROM supervisor_execution_session s JOIN supervisor_dispatch_ticket t "
        "ON t.id=s.ticket_id AND t.workspace_id=s.workspace_id WHERE s.ticket_id=%s "
        "AND s.workspace_id=%s AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() "
        "AND t.accepted_at IS NOT NULL AND t.revoked_at IS NULL FOR UPDATE OF s,t",
        (session_id, workspace_id),
    ).fetchone()
    if current is None:
        raise Refused("supervisor session unavailable")
    try:
        manifest = runners.assert_manual_attempt_authorized(
            conn,
            workspace_id=workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            runner_id=str(row["runner_id"]),
            lease_id=str(row["lease_id"]),
            epoch=int(row["epoch"]),
        )
    except runners.DispatchRefused as exc:
        raise Refused("supervisor session unavailable") from exc
    return dict(row), manifest


def check_startup_authority(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
) -> dict[str, Any]:
    """Fresh pre-action execution authority, NOT reader-settings consent or physical proof.

    No action, approval, event or lifetime extension. The same locks as intent admission serialize
    this read; after the first intent, even a resolved one, this is no longer a startup path.
    """
    row, manifest = _live(conn, workspace_id, session_id, token)
    if conn.execute(
        "SELECT 1 FROM runner_action WHERE run_id=%s AND attempt_id=%s LIMIT 1",
        (row["run_id"], row["attempt_id"]),
    ).fetchone():
        raise Refused("startup authority unavailable after action admission")
    policy_row = conn.execute(
        "SELECT navigator_policy FROM journey_version WHERE id=%s",
        (manifest["journeyVersionId"],),
    ).fetchone()
    if policy_row is None:
        raise Refused("startup policy unavailable")
    policy = policy_row["navigator_policy"]
    wall = policy.get("wallTimeSeconds")
    if type(wall) is not int or wall < 1 or digest(policy) != manifest["navigatorPolicyDigest"]:
        raise Refused("startup policy unavailable")
    bounds = conn.execute(
        "SELECT s.expires_at AS session_expiry,l.deadline_at AS lease_expiry,"
        "a.expires_at AS approval_expiry,clock_timestamp() AS observed_at "
        "FROM supervisor_execution_session s JOIN desktop_lease l ON l.id=%s "
        "JOIN run r ON r.id=%s JOIN approval a ON a.id=r.authorization_id "
        "WHERE s.ticket_id=%s AND s.workspace_id=%s",
        (row["lease_id"], row["run_id"], session_id, workspace_id),
    ).fetchone()
    if bounds is None:
        raise Refused("startup authority unavailable")
    expires = min(
        bounds["session_expiry"],
        bounds["lease_expiry"],
        bounds["approval_expiry"],
        parse_rfc3339_utc(manifest["expiresAt"], field="expiresAt"),
        row["created_at"] + timedelta(seconds=min(wall, manifest["wallTimeBudgetSeconds"])),
    )
    if expires <= bounds["observed_at"]:
        raise Refused("startup execution budget expired")
    return {
        "sessionId": session_id,
        "reference": {
            "workspaceId": workspace_id,
            "runId": str(row["run_id"]),
            "attemptId": str(row["attempt_id"]),
            "runnerId": str(row["runner_id"]),
            "leaseId": str(row["lease_id"]),
            "epoch": int(row["epoch"]),
        },
        "expiresAt": to_rfc3339_utc(expires),
        "meaning": "EXECUTION_AUTHORITY_RECHECKED_NOT_READER_START_CONSENT",
    }


def check_reader_startup_consent(
    conn: psycopg.Connection[Any], *, workspace_id: str, session_id: str, token: str
) -> dict[str, Any]:
    """Authenticate live execution, then bind/recheck the separate operator grant atomically.

    No reader is started. Binding a grant cannot extend session/run authority or authorize TCC.
    """
    authority = check_startup_authority(
        conn, workspace_id=workspace_id, session_id=session_id, token=token
    )
    consent = reader_startup_consents.bind_and_check(
        conn, workspace_id=workspace_id, session_id=session_id
    )
    return {
        "sessionId": session_id,
        "reference": authority["reference"],
        "consentId": consent["consentId"],
        "manifestDigest": consent["manifestDigest"],
        "desktopSessionKey": consent["desktopSessionKey"],
        "runnerProfileDigest": consent["runnerProfileDigest"],
        "effectsDigest": consent["effectsDigest"],
        "expiresAt": to_rfc3339_utc(
            min(
                parse_rfc3339_utc(authority["expiresAt"], field="expiresAt"),
                parse_rfc3339_utc(consent["expiresAt"], field="expiresAt"),
            )
        ),
        "meaning": "OPERATOR_STARTUP_CONSENT_RECHECKED_NOT_PHYSICAL_PROOF",
    }


def retain_action_intent(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
    command: dict[str, Any],
) -> dict[str, Any]:
    """Reserve one sequential unresolved intent. Never a dispatch/result or canonical observation.

    The physical supervisor must still validate observed origin/focus, deployed bytes, effects,
    local watchdog and its fsynced journal immediately before touching the OS. This acknowledgement
    deliberately does not mint permission to bypass those physical checks.
    """
    row, manifest = _live(conn, workspace_id, session_id, token)
    allowed_fields = {"action", "sequence", "origin", "keyChord", "textValueRef"}
    if set(command) - allowed_fields or not {"action", "sequence", "origin"} <= set(command):
        raise Refused("invalid action intent")
    action, sequence, origin = command["action"], command["sequence"], command["origin"]
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS or type(sequence) is not int:
        raise Refused("invalid action intent")
    if not isinstance(origin, str) or len(origin) > 2048:
        raise Refused("invalid action origin")
    if ("keyChord" in command) != (action == "KEY_CHORD") or ("textValueRef" in command) != (
        action == "TYPE_TEXT"
    ):
        raise Refused("invalid action fields")
    policy_row = conn.execute(
        "SELECT navigator_policy FROM journey_version WHERE id=%s", (manifest["journeyVersionId"],)
    ).fetchone()
    if policy_row is None:
        raise Refused("sealed action policy unavailable")
    policy = policy_row["navigator_policy"]
    policy_max, policy_wall = policy.get("maxActions"), policy.get("wallTimeSeconds")
    if (
        type(policy_max) is not int
        or type(policy_wall) is not int
        or policy_max < 1
        or policy_wall < 1
    ):
        raise Refused("sealed action policy budgets unavailable")
    if digest(policy) != manifest["navigatorPolicyDigest"] or action not in policy.get(
        "allowedActions", []
    ):
        raise Refused("sealed action policy unavailable")
    environment = conn.execute(
        "SELECT e.allowed_origins FROM environment_manifest e JOIN sealed_manifest s "
        "ON s.environment_manifest_id=e.id WHERE s.run_id=%s",
        (row["run_id"],),
    ).fetchone()
    try:
        normalized = str(normalize_origin(origin))
    except ValueError as exc:
        raise Refused("invalid action origin") from exc
    if environment is None or normalized not in environment["allowed_origins"]:
        raise Refused("action origin is outside the sealed environment")
    chord = command.get("keyChord")
    if action == "KEY_CHORD":
        platform = conn.execute(
            "SELECT platform FROM runner WHERE id=%s", (row["runner_id"],)
        ).fetchone()
        if (
            not isinstance(chord, str)
            or platform is None
            or chord not in ALLOWED_KEY_CHORDS.get(platform["platform"], frozenset())
            or chord not in policy.get("allowedKeyChords", [])
        ):
            raise Refused("key chord is outside the sealed policy")
    text: str | None = None
    if action == "TYPE_TEXT":
        reference = command.get("textValueRef")
        fixture = conn.execute(
            "SELECT navigator_values FROM run_fixture_instance WHERE run_id=%s", (row["run_id"],)
        ).fetchone()
        if not isinstance(reference, str) or fixture is None:
            raise Refused("action fixture value unavailable")
        value = fixture["navigator_values"].get(reference)
        if not isinstance(value, str) or len(value) > 4096:
            raise Refused("action fixture value unavailable")
        text = value
    usage = conn.execute(
        "SELECT count(*) AS n,coalesce(max(action_sequence),0) AS last,"
        "count(*) FILTER(WHERE result_at IS NULL OR result_status='AMBIGUOUS') AS unresolved,"
        "count(*) FILTER(WHERE action='STOP') AS stops "
        "FROM runner_action WHERE run_id=%s AND attempt_id=%s",
        (row["run_id"], row["attempt_id"]),
    ).fetchone()
    assert usage is not None
    elapsed = (datetime.now(UTC) - row["created_at"]).total_seconds()
    if (
        usage["unresolved"]
        or usage["stops"]
        or sequence != usage["last"] + 1
        or usage["n"] >= min(manifest["actionBudget"], policy_max)
        or elapsed >= min(manifest["wallTimeBudgetSeconds"], policy_wall)
    ):
        raise Refused("action sequence, unresolved intent or budget prevents admission")
    action_id = runners.record_action_intent(
        conn,
        workspace_id=workspace_id,
        lease_id=str(row["lease_id"]),
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
        epoch=int(row["epoch"]),
        action_sequence=sequence,
        action=action,
        origin=normalized,
        key_chord=chord,
        text_value=text,
    )
    session_evidence.emit(
        conn,
        row,
        stream="actions",
        sequence=2 * sequence - 1,
        source_id=action_id + ":intent",
        event_type="ACTION_INTENT",
        source={
            "actionId": action_id,
            "sequence": sequence,
            "action": action,
            "origin": normalized,
            **({"keyChord": chord} if chord is not None else {}),
            **({"textValueRef": command["textValueRef"]} if action == "TYPE_TEXT" else {}),
        },
    )
    return {
        "actionId": action_id,
        "sessionId": session_id,
        "sequence": sequence,
        "meaning": "ACTION_INTENT_RETAINED",
    }


def _action(conn: psycopg.Connection[Any], row: dict[str, Any], action_id: str) -> dict[str, Any]:
    action = conn.execute(
        "SELECT * FROM runner_action WHERE id=%s AND workspace_id=%s AND run_id=%s "
        "AND attempt_id=%s AND lease_id=%s AND epoch=%s FOR UPDATE",
        (
            action_id,
            row["workspace_id"],
            row["run_id"],
            row["attempt_id"],
            row["lease_id"],
            row["epoch"],
        ),
    ).fetchone()
    if action is None or action["result_at"] is not None:
        raise Refused("unresolved action identity unavailable")
    return dict(action)


def commit_action_dispatch(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
    action_id: str,
    origin: str,
) -> dict[str, Any]:
    """Commit at most one delivery of the retained command, before the local OS boundary.

    This is a dispatch commitment, NOT proof an OS action happened. Lost acknowledgement means
    reconciliation/quarantine, never another delivery. The desktop still checks physical state and
    fsyncs its local journal before invoking its reader adapter.
    """
    row, manifest = _live(conn, workspace_id, session_id, token)
    action = _action(conn, row, action_id)
    try:
        normalized = str(normalize_origin(origin))
    except ValueError as exc:
        raise Refused("current action origin unavailable") from exc
    if action["dispatched_at"] is not None or normalized != action["origin"]:
        raise Refused("action already committed or origin changed")
    policy_row = conn.execute(
        "SELECT navigator_policy FROM journey_version WHERE id=%s", (manifest["journeyVersionId"],)
    ).fetchone()
    if (
        policy_row is None
        or digest(policy_row["navigator_policy"]) != manifest["navigatorPolicyDigest"]
    ):
        raise Refused("sealed action policy unavailable")
    policy = policy_row["navigator_policy"]
    elapsed = (datetime.now(UTC) - row["created_at"]).total_seconds()
    if elapsed >= min(manifest["wallTimeBudgetSeconds"], policy["wallTimeSeconds"]):
        raise Refused("action wall-time budget expired")
    if action["action"] == "TYPE_TEXT":
        fixture = conn.execute(
            "SELECT navigator_values FROM run_fixture_instance WHERE run_id=%s", (row["run_id"],)
        ).fetchone()
        if fixture is None or action["text_value"] not in fixture["navigator_values"].values():
            raise Refused("retained fixture text no longer available")
    runners.mark_action_dispatched(conn, action_id=action_id)
    command: dict[str, Any] = {
        "actionId": action_id,
        "sequence": action["action_sequence"],
        "action": action["action"],
    }
    if action["key_chord"] is not None:
        command["keyChord"] = action["key_chord"]
    if action["text_value"] is not None:
        command["text"] = action["text_value"]
    return {"sessionId": session_id, "command": command, "meaning": "ACTION_DISPATCH_COMMITTED"}


def retain_reader_observation(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
    action_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Retain bounded, action-bound reader evidence; never an independent effect receipt.

    This serial client has one in-flight action and refuses gaps rather than acknowledging a
    staged record as durable canonical evidence. The submitted digest covers the original source;
    the retained source/digest explicitly cover a fixture-redacted projection. Raw text is not
    logged or persisted here. Neither digest proves that a physical reader produced the bytes.
    """
    row, _ = _live(conn, workspace_id, session_id, token)
    action = _action(conn, row, action_id)
    if action["dispatched_at"] is None:
        raise Refused("reader evidence requires a dispatched unresolved action")
    if set(record) != {"producerSequence", "sourceRecordDigest", "sourceRecord"}:
        raise Refused("invalid reader source envelope")
    sequence, source = record["producerSequence"], record["sourceRecord"]
    if (
        type(sequence) is not int
        or not 1 <= sequence <= action["action_sequence"]
        or not isinstance(source, dict)
    ):
        raise Refused("invalid reader source envelope")
    unknown = source.get("provenance") == "CAPTURE_UNKNOWN"
    fields = {"actionId", "actionSequence", "capturedAtUtc"} | (
        {"provenance", "reason"} if unknown else {"phrase"}
    )
    if (
        set(source) != fields
        or source["actionId"] != action_id
        or type(source["actionSequence"]) is not int
        or source["actionSequence"] != action["action_sequence"]
        or not isinstance(source["capturedAtUtc"], str)
        or len(source["capturedAtUtc"]) > 40
    ):
        raise Refused("reader source identity unavailable")
    text_key = "reason" if unknown else "phrase"
    if not isinstance(source[text_key], str) or len(source[text_key]) > 8192:
        raise Refused("reader source exceeds text bound")
    try:
        if len(json.dumps(source, ensure_ascii=False).encode("utf-8")) > 32768:
            raise ValueError("size")
        original_digest = digest(source)
        source_time = parse_rfc3339_utc(source["capturedAtUtc"])
    except (ValueError, UnicodeError) as exc:
        raise Refused("invalid reader source content") from exc
    if record["sourceRecordDigest"] != original_digest:
        raise Refused("reader source digest mismatch")
    principal = MachinePrincipal(
        service_identity=ServiceIdentity.SUPERVISOR,
        workspace_id=workspace_id,
        credential_id=session_id,
        run_id=str(row["run_id"]),
        lease_id=str(row["lease_id"]),
    )
    assert_may_submit_event(principal, "READER_OBSERVATION")
    fixture = conn.execute(
        "SELECT f.navigator_values,f.observer_config,f.nonce,r.manifest_digest "
        "FROM run_fixture_instance f JOIN run r ON r.id=f.run_id WHERE f.run_id=%s",
        (row["run_id"],),
    ).fetchone()
    if fixture is None:
        raise Refused("reader evidence redaction context unavailable")
    private_values = {
        value
        for value in [
            *fixture["navigator_values"].values(),
            *fixture["observer_config"].values(),
            fixture["nonce"],
        ]
        if isinstance(value, str) and value
    }
    retained = dict(source)
    # One substitution pass: a replacement marker must not itself be rewritten by another value.
    if private_values:
        pattern = "|".join(
            re.escape(value) for value in sorted(private_values, key=len, reverse=True)
        )
        retained[text_key] = re.sub(pattern, "[REDACTED_FIXTURE]", source[text_key])
    producer = f"supervisor:{session_id}:reader"
    payload = {
        "producerId": producer,
        "producerSequence": sequence,
        "sourceRecordId": action_id,
        "submittedSourceRecordDigest": original_digest,
        "sourceRecordDigest": digest(retained),
        "sourceRecord": retained,
        "redaction": "EXACT_FIXTURE_VALUES",
        "serviceIdentity": "SUPERVISOR",
        "eventType": "READER_OBSERVATION",
    }
    try:
        admitted = sequencer.admit_record(
            conn,
            workspace_id=workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            lease_epoch=int(row["epoch"]),
            producer_id=producer,
            source_record_id=action_id,
            producer_sequence=sequence,
            event_type="READER_OBSERVATION",
            manifest_digest=str(fixture["manifest_digest"]),
            payload=payload,
            source_time=source_time,
        )
    except sequencer.SequencerError as exc:
        raise Refused("reader evidence conflict, gap or closed stream") from exc
    return {
        "sessionId": session_id,
        "actionId": action_id,
        "eventId": admitted.event_id,
        "producerSequence": sequence,
        "submittedSourceRecordDigest": original_digest,
        "meaning": "READER_OBSERVATION_RETAINED",
    }


def record_action_completion(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
    action_id: str,
    status: str,
) -> dict[str, Any]:
    """Retain an authenticated action result once; never a run/assertion verdict.

    Positive results cannot resurrect expired/revoked sessions or overwrite ambiguity. If fresh
    authority is unavailable, local evidence must be retained and recovery keeps the run unknown.
    """
    if status not in {"SUCCEEDED", "FAILED", "AMBIGUOUS"}:
        raise Refused("invalid action result")
    row, _ = _live(conn, workspace_id, session_id, token)
    action = _action(conn, row, action_id)
    if action["dispatched_at"] is None and status != "AMBIGUOUS":
        raise Refused("a non-dispatched intent cannot report a known action result")
    session_evidence.emit(
        conn,
        row,
        stream="actions",
        sequence=2 * action["action_sequence"],
        source_id=action_id + ":result",
        event_type="ACTION_RESULT",
        source={"actionId": action_id, "sequence": action["action_sequence"], "status": status},
    )
    if status == "AMBIGUOUS":
        runners.mark_action_ambiguous(
            conn, action_id=action_id, reason=AmbiguityReason.ACTION_RESULT_NEVER_ARRIVED
        )
        runners.interrupt_manual_handoff(
            conn,
            workspace_id=workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            runner_id=str(row["runner_id"]),
            lease_id=str(row["lease_id"]),
            epoch=int(row["epoch"]),
        )
    else:
        runners.record_action_result(conn, action_id=action_id, status=status)
    return {
        "sessionId": session_id,
        "actionId": action_id,
        "status": status,
        "meaning": "ACTION_RESULT_RETAINED",
    }


def finish_session(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    session_id: str,
    token: str,
    stop_action_id: str,
    reader_sequence: int,
) -> dict[str, Any]:
    """Acknowledge normal STOP and hand off to FINALIZING, never COMPLETED or PASS.

    Missing artifacts remain declared and block outcome admission. The independent observer must
    already have closed its own final sample; the supervisor cannot close it on its behalf.
    """
    row, _ = _live(conn, workspace_id, session_id, token)
    state_before = runs.load_run(conn, run_id=str(row["run_id"])).state
    actions = conn.execute(
        "SELECT * FROM runner_action WHERE run_id=%s AND attempt_id=%s ORDER BY action_sequence",
        (row["run_id"], row["attempt_id"]),
    ).fetchall()
    if (
        state_before.unresolved_action
        or not actions
        or type(reader_sequence) is not int
        or reader_sequence < 0
        or str(actions[-1]["id"]) != stop_action_id
        or actions[-1]["action"] != "STOP"
        or actions[-1]["result_status"] != "SUCCEEDED"
        or any(
            a["dispatched_at"] is None
            or a["result_at"] is None
            or a["result_status"] == "AMBIGUOUS"
            or str(a["lease_id"]) != str(row["lease_id"])
            or int(a["epoch"]) != int(row["epoch"])
            for a in actions
        )
    ):
        raise Refused("current successful STOP and resolved actions are required")
    required = session_evidence.requirements(conn, row)
    declared = conn.execute(
        "SELECT kind,producer_id FROM required_artifact WHERE run_id=%s",
        (row["run_id"],),
    ).fetchall()
    if not set(required.items()) <= {(r["kind"], r["producer_id"]) for r in declared}:
        raise Refused("execution has no original artifact declaration; no historical backfill")
    reader = conn.execute(
        "SELECT source_record_id FROM producer_source_record "
        "WHERE attempt_id=%s AND producer_id=%s",
        (row["attempt_id"], required["SPEECH_TRANSCRIPT"]),
    ).fetchall()
    if reader_sequence != len(reader) or {r["source_record_id"] for r in reader} != {
        str(a["id"]) for a in actions if a["action"] != "STOP"
    }:
        raise Refused("reader evidence does not cover the complete action tail")
    observer = conn.execute(
        "SELECT e.payload FROM canonical_event e "
        "JOIN producer_source_record p ON p.event_id=e.event_id "
        "JOIN producer_stream s ON s.attempt_id=p.attempt_id AND s.producer_id=p.producer_id "
        "WHERE p.attempt_id=%s AND p.producer_id=%s AND s.closed_at_sequence=p.producer_sequence "
        "AND s.admitted_through=s.closed_at_sequence",
        (row["attempt_id"], required["EFFECT_RECEIPT"]),
    ).fetchone()
    if (
        observer is None
        or observer["payload"].get("sourceRecord", {}).get("finalSample") is not True
        or observer["payload"]["sourceRecord"].get("afterActionSequence")
        != actions[-1]["action_sequence"]
    ):
        raise Refused("independent observer final sample/tail is unavailable")
    session_evidence.emit(
        conn,
        row,
        stream="lifecycle",
        sequence=3,
        source_id="execution-stopped",
        event_type="RUN_FINISHED",
        source={"stopActionId": stop_action_id, "meaning": "STOP_ACKNOWLEDGED"},
    )
    for producer, sequence in (
        (required["SPEECH_TRANSCRIPT"], reader_sequence),
        (required["ACTION_TRACE"], len(actions) * 2),
        (required["PREFLIGHT_RECORD"], 3),
    ):
        sequencer.close_producer_stream(
            conn,
            workspace_id=workspace_id,
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            producer_id=producer,
            final_producer_sequence=sequence,
        )
    now = to_rfc3339_utc(datetime.now(UTC))
    conn.execute(
        "UPDATE desktop_lease SET stop_acknowledged_at=%s,stop_acknowledged_epoch=epoch,"
        "released_at=%s,release_reason='STOP_ACKNOWLEDGED' WHERE id=%s",
        (now, now, row["lease_id"]),
    )
    conn.execute(
        "UPDATE runner SET status='PREFLIGHT_REQUIRED',updated_at=%s,revision=revision+1 "
        "WHERE id=%s",
        (now, row["runner_id"]),
    )
    state = runs.apply_transition(
        conn,
        run_id=str(row["run_id"]),
        reducer=lambda current: reducers.progress(
            reducers.acknowledge_stop(
                current,
                acknowledged_at=now,
                epoch=int(row["epoch"]),
            )
        ),
        operation_id=str(uuid5(NAMESPACE_URL, "accessforge:manual-finish:" + session_id)),
        topic="run.finalizing",
        actor_service="authenticated-supervisor",
        audit_action="RUN_EXECUTION_STOPPED",
    )
    conn.execute(
        "UPDATE supervisor_execution_session SET revoked_at=%s WHERE ticket_id=%s",
        (now, session_id),
    )
    conn.execute(
        "UPDATE supervisor_dispatch_ticket SET revoked_at=%s WHERE id=%s", (now, session_id)
    )
    missing = artifacts.missing_required_artifacts(
        conn,
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
    )
    return {
        "sessionId": session_id,
        "runId": str(row["run_id"]),
        "status": state.status.value,
        "outcome": state.outcome.value,
        "missingArtifactCount": len(missing),
        "meaning": "EXECUTION_STOPPED_AWAITING_FINALIZATION",
    }
