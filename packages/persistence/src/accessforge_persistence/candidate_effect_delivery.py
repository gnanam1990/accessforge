"""Worker-only one-shot candidate POST delivery, not an independent effect observation."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl

import psycopg

from accessforge_domain.canonical import digest

from . import candidate_builds as builds
from . import candidate_effects as permits
from . import candidate_endpoints as endpoints
from . import candidate_regressions as regressions
from . import candidate_runs, runners, supervisor_sessions

Refused = permits.Refused

# Closed reference-service-request/1 destinations. Alias names carry field meaning, not a pool
# of values that may be substituted into any control. Missing aliases never widen permission.
_FIELD_VALUE_KEYS = MappingProxyType(
    {
        "full_name": ("full_name",),
        "email": ("email", "email_invalid", "email_valid"),
        "category": ("category",),
        "description": ("description",),
    }
)


def _live(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    permit_id: str,
) -> dict[str, Any]:
    stored = conn.execute(
        "SELECT * FROM candidate_action_effect_permit WHERE id=%s", (permit_id,)
    ).fetchone()
    if stored is None or str(stored["regression_attempt_id"]) != claim.attempt_id:
        raise Refused("original candidate action permission unavailable")
    permits._view(stored)
    grant = stored["grant_payload"]
    # Same order as the supervisor result path. Never lock the regression before its runner/run.
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (grant["runnerId"],))
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (grant["runId"],))
    conn.execute("SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE", (grant["leaseId"],))
    conn.execute(
        "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
        "WHERE r.id=%s FOR SHARE OF a",
        (grant["runId"],),
    )
    session = conn.execute(
        "SELECT 1 FROM supervisor_execution_session s JOIN supervisor_dispatch_ticket t "
        "ON t.id=s.ticket_id AND t.workspace_id=s.workspace_id WHERE s.ticket_id=%s "
        "AND s.workspace_id=%s AND t.run_id=%s AND t.lease_id=%s AND t.epoch=%s "
        "AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() "
        "AND t.accepted_at IS NOT NULL AND t.revoked_at IS NULL FOR UPDATE OF s,t",
        (
            grant["sessionId"],
            grant["workspaceId"],
            grant["runId"],
            grant["leaseId"],
            grant["leaseEpoch"],
        ),
    ).fetchone()
    if session is None:
        raise Refused("original supervisor action session expired or revoked")
    try:
        manifest = runners.assert_manual_attempt_authorized(
            conn,
            workspace_id=grant["workspaceId"],
            run_id=grant["runId"],
            attempt_id=grant["attemptId"],
            runner_id=grant["runnerId"],
            lease_id=grant["leaseId"],
            epoch=grant["leaseEpoch"],
        )
        regressions.assert_active(conn, claim=claim)
        endpoints.assert_live(conn, claim=claim)
    except (runners.DispatchRefused, regressions.Refused) as exc:
        raise Refused("current worker or form-effect authority unavailable") from exc
    action = conn.execute(
        "SELECT * FROM runner_action WHERE id=%s AND run_id=%s AND attempt_id=%s "
        "AND lease_id=%s AND epoch=%s FOR UPDATE",
        (
            grant["actionId"],
            grant["runId"],
            grant["attemptId"],
            grant["leaseId"],
            grant["leaseEpoch"],
        ),
    ).fetchone()
    endpoint = endpoints._record(conn, claim)
    if (
        action is None
        or action["result_at"] is not None
        or action["dispatched_at"] is None
        or not (
            action["action"] == "ACTIVATE"
            or (action["action"] == "KEY_CHORD" and action.get("key_chord") in {"ENTER", "SPACE"})
        )
        or digest(manifest) != grant["manifestDigest"]
        or "FORM_SUBMIT" not in manifest["permittedEffects"]
        or grant["endpointBindingDigest"] != endpoint["binding_digest"]
        or grant["method"] != "POST"
        or grant["effect"] != "FORM_SUBMIT"
        or grant["path"] != endpoint["plan"]["path"]
    ):
        raise Refused("candidate form-effect action or binding changed")
    locked = conn.execute(
        "SELECT * FROM candidate_action_effect_permit WHERE id=%s FOR UPDATE",
        (permit_id,),
    ).fetchone()
    if locked is None or locked["expires_at"] <= builds._moment(conn, None):
        raise Refused("candidate form-effect permission expired")
    permits._view(locked)
    return locked


def begin(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    path: str,
    body: str,
    allow_preview: bool = False,
) -> dict[str, Any] | None:
    """Caller must COMMIT before forwarding HTTP; uncertain commit never returns a send token."""
    binding = conn.execute(
        "SELECT run_id FROM candidate_run_binding WHERE regression_attempt_id=%s",
        (claim.attempt_id,),
    ).fetchone()
    if binding is None:
        if not allow_preview:
            raise Refused("candidate session cannot submit preview effects before its run binding")
        regressions.assert_active(conn, claim=claim)
        # A concurrent bind that won the regression lock must not inherit preview POST authority.
        candidate_runs.assert_request(conn, attempt_id=claim.attempt_id, method="POST")
        return None
    eligible = conn.execute(
        "SELECT p.id FROM candidate_action_effect_permit p "
        "JOIN runner_action a ON a.id=p.action_id "
        "WHERE p.regression_attempt_id=%s AND a.result_at IS NULL AND p.consumed_at IS NULL "
        "AND p.expires_at>clock_timestamp() ORDER BY p.granted_at LIMIT 2",
        (claim.attempt_id,),
    ).fetchall()
    if len(eligible) != 1:
        raise Refused("one original unconsumed form permission required; no replay")
    identifier = str(eligible[0]["id"])
    permit = _live(conn, claim=claim, permit_id=identifier)
    grant = permit["grant_payload"]
    if permit["consumed_at"] is not None or grant["path"] != path:
        raise Refused("form permission already consumed or wrong route")
    fixture = conn.execute(
        "SELECT navigator_values FROM run_fixture_instance WHERE run_id=%s", (grant["runId"],)
    ).fetchone()
    try:
        pairs = parse_qsl(
            body,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
            encoding="utf-8",
            errors="strict",
        )
    except (ValueError, UnicodeError) as exc:
        raise Refused("bounded reference form body required") from exc
    if (
        len(body.encode("utf-8")) > 8192
        or fixture is None
        or len({key for key, _ in pairs}) != len(pairs)
        or any(key not in _FIELD_VALUE_KEYS for key, _ in pairs)
        or any(
            value
            and value
            not in {
                fixture["navigator_values"][alias]
                for alias in _FIELD_VALUE_KEYS[key]
                if alias in fixture["navigator_values"]
            }
            for key, value in pairs
        )
    ):
        raise Refused("reference form values are outside the sealed synthetic fixture")
    request_digest = digest({"method": "POST", "path": path, "body": body})
    conn.execute(
        "UPDATE candidate_action_effect_permit SET consumed_at=clock_timestamp() WHERE id=%s",
        (identifier,),
    )
    conn.execute(
        "INSERT INTO candidate_effect_delivery(permit_id,workspace_id,request_digest) "
        "VALUES(%s,%s,%s)",
        (identifier, permit["workspace_id"], request_digest),
    )
    return permits._view(permit)


def check(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    permission: dict[str, Any],
) -> dict[str, Any]:
    permit = _live(conn, claim=claim, permit_id=permission["permitId"])
    if permit["consumed_at"] is None or permits._view(permit) != permission:
        raise Refused("original committed form permission required")
    delivery = conn.execute(
        "SELECT * FROM candidate_effect_delivery WHERE permit_id=%s FOR UPDATE", (permit["id"],)
    ).fetchone()
    if delivery is None or delivery["response_digest"] is not None:
        raise Refused("original unresolved delivery required; no replay")
    return delivery


def retain_response(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    claim: regressions.RegressionClaim,
    permission: dict[str, Any],
    response: dict[str, Any],
) -> None:
    check(conn, claim=claim, permission=permission)
    if (
        set(response) != {"status", "body"}
        or type(response["status"]) is not int
        or response["status"] not in {200, 201, 404, 409, 422}
        or not isinstance(response["body"], str)
        or len(response["body"].encode("utf-8")) > 262144
    ):
        raise Refused("bounded candidate form response unavailable")
    conn.execute(
        "UPDATE candidate_effect_delivery SET response_digest=%s,response_status=%s,"
        "responded_at=clock_timestamp() "
        "WHERE permit_id=%s AND response_digest IS NULL",
        (digest(response), response["status"], permission["permitId"]),
    )


def assert_action_result(conn: psycopg.Connection[Any], *, action_id: str) -> None:
    """Caller holds original action/run locks; no known result may hide an unresolved POST."""
    unresolved = conn.execute(
        "SELECT 1 FROM candidate_action_effect_permit p "
        "LEFT JOIN candidate_effect_delivery d ON d.permit_id=p.id "
        "WHERE p.action_id=%s AND ((p.consumed_at IS NOT NULL AND d.response_digest IS NULL) "
        "OR (p.consumed_at IS NULL AND p.expires_at>clock_timestamp()))",
        (action_id,),
    ).fetchone()
    if unresolved is not None:
        raise Refused("candidate form response is unresolved; retain ambiguity, never replay")


def status(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    session_id: str,
    action_id: str,
    token: str,
) -> dict[str, Any]:
    """Read-only wait state. Does not consume/rearm permission or retry a request."""
    session, _ = supervisor_sessions._live(conn, workspace_id, session_id, token)
    supervisor_sessions._action(conn, session, action_id)
    permit = conn.execute(
        "SELECT * FROM candidate_action_effect_permit WHERE action_id=%s", (action_id,)
    ).fetchone()
    if permit is None:
        raise Refused("original form permission unavailable")
    view = permits._view(permit)
    delivery = conn.execute(
        "SELECT response_digest FROM candidate_effect_delivery WHERE permit_id=%s", (permit["id"],)
    ).fetchone()
    if permit["consumed_at"] is not None:
        phase = (
            "RESPONSE_RETAINED"
            if delivery is not None and delivery["response_digest"] is not None
            else "IN_FLIGHT"
        )
    else:
        phase = "CLOSED_UNUSED" if permit["expires_at"] <= builds._moment(conn, None) else "OPEN"
    return {
        "sessionId": session_id,
        "actionId": action_id,
        "permitId": view["permitId"],
        "phase": phase,
        "meaning": "FORM_TRANSPORT_STATE_NOT_EFFECT_PROOF",
    }
