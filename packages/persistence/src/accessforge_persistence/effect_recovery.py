"""Bounded historical form-transport inspection; no dispatch, reset or outcome inference."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from accessforge_domain.timestamps import to_rfc3339_utc

from . import candidate_effects


class Refused(Exception):
    pass


def _time(value: Any) -> str | None:
    return None if value is None else to_rfc3339_utc(value)


def _item(row: dict[str, Any]) -> dict[str, Any]:
    try:
        permit = candidate_effects._view(row)
        grant = row["grant_payload"]
        if (
            grant["runId"] != str(row["requested_run_id"])
            or grant["attemptId"] != str(row["action_attempt_id"])
            or grant["leaseId"] != str(row["action_lease_id"])
            or grant["leaseEpoch"] != row["action_epoch"]
        ):
            raise Refused("original permission/action binding differs")
        if row["consumed_at"] is None:
            if row["delivery_id"] is not None:
                raise Refused("unconsumed permission has an inconsistent delivery record")
            phase = "OPEN" if row["expires_at"] > row["snapshot_at"] else "CLOSED_UNUSED"
        elif row["delivery_id"] is None:
            phase = "DELIVERY_RECORD_MISSING"
        elif row["response_digest"] is None:
            phase = "UNCONFIRMED"
        else:
            if (
                row["response_status"] not in (200, 201, 404, 409, 422)
                or row["responded_at"] is None
            ):
                raise Refused("retained transport response is inconsistent")
            phase = "RESPONSE_RETAINED"
        return {
            "permitId": permit["permitId"],
            "actionId": permit["actionId"],
            "attemptId": str(row["action_attempt_id"]),
            "actionSequence": row["action_sequence"],
            "action": row["action"],
            "actionResult": row["result_status"],
            "phase": phase,
            "grantedAt": _time(row["granted_at"]),
            "expiresAt": permit["expiresAt"],
            "consumedAt": _time(row["consumed_at"]),
            "responseRecordedAt": _time(row["responded_at"]),
            "requestDigest": row["request_digest"],
            "responseDigest": row["response_digest"],
            "responseStatus": row["response_status"],
            "actionResultAt": _time(row["result_at"]),
            "leaseId": str(row["action_lease_id"]),
            "leaseReleasedAt": _time(row["released_at"]),
            "stopAcknowledgedAt": _time(row["stop_acknowledged_at"]),
            "runnerQuarantined": row["runner_quarantined_at"] is not None,
            "endpointState": row["endpoint_state"],
            "endpointCleanupConfirmed": row["cleanup_confirmed"],
            "requiresInvestigation": phase in {"UNCONFIRMED", "DELIVERY_RECORD_MISSING"}
            or row["result_status"] == "AMBIGUOUS",
        }
    except (KeyError, TypeError, ValueError, candidate_effects.Refused) as exc:
        raise Refused("original form-transport history integrity unavailable") from exc


def read(
    conn: psycopg.Connection[Any],
    *,
    run_id: str,
    limit: int = 50,
    after: str | None = None,
) -> dict[str, Any] | None:
    """One statement snapshot. Cursor orders original permit UUIDs, not event chronology.

    Workspace RLS and the caller's evidence-read permission are required. Empty history does not
    prove zero application effects. This works after session expiry without renewing authority.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise Refused("recovery report limit must be between 1 and 100")
    try:
        run = str(UUID(run_id))
        cursor = None if after is None else str(UUID(after))
    except (ValueError, TypeError, AttributeError) as exc:
        raise Refused("recovery report identity unavailable") from exc
    rows = conn.execute(
        "SELECT r.id AS requested_run_id,r.status AS run_status,r.outcome AS run_outcome,"
        "r.quarantined AS run_quarantined,statement_timestamp() AS snapshot_at,page.* "
        "FROM run r LEFT JOIN LATERAL ("
        " SELECT p.*,a.attempt_id AS action_attempt_id,a.lease_id AS action_lease_id,"
        " a.epoch AS action_epoch,a.action_sequence,a.action,a.result_status,a.result_at,"
        " l.released_at,l.stop_acknowledged_at,n.quarantined_at AS runner_quarantined_at,"
        " e.state AS endpoint_state,e.cleanup_confirmed,d.permit_id AS delivery_id,"
        " d.request_digest,d.response_digest,d.response_status,d.responded_at "
        " FROM candidate_action_effect_permit p JOIN runner_action a ON a.id=p.action_id "
        " JOIN desktop_lease l ON l.id=a.lease_id JOIN runner n ON n.id=l.runner_id "
        " LEFT JOIN candidate_endpoint e ON e.attempt_id=p.regression_attempt_id "
        " LEFT JOIN candidate_effect_delivery d ON d.permit_id=p.id "
        " WHERE a.run_id=r.id AND (%s::uuid IS NULL OR p.id>%s::uuid) "
        " ORDER BY p.id LIMIT %s"
        ") page ON true WHERE r.id=%s AND r.workspace_id=current_workspace_id() ORDER BY page.id",
        (cursor, cursor, limit + 1, run),
    ).fetchall()
    if not rows:
        return None
    root = rows[0]
    retained = [row for row in rows if row["id"] is not None]
    page = retained[:limit]
    return {
        "runId": run,
        "observedAt": _time(root["snapshot_at"]),
        "runStatus": root["run_status"],
        "runOutcome": root["run_outcome"],
        "runQuarantined": root["run_quarantined"],
        "items": [_item(row) for row in page],
        "nextCursor": str(page[-1]["id"]) if len(retained) > limit else None,
        "providesRetryAuthority": False,
        "providesResetAuthority": False,
        "meaning": "FORM_TRANSPORT_HISTORY_NOT_EFFECT_PROOF",
    }
