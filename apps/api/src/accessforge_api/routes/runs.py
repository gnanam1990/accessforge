"""Runs: request, inspect, cancel, retry, and evidence replay."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain import reducers
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import evidence, runners, runs

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["runs"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]


def _run_view(row: dict[str, Any]) -> dict[str, Any]:
    """One run, with status and outcome kept as separate fields.

    Never merged. Status says how the run ended; outcome says what it established, and a run can
    end cleanly having established nothing. A single "state" field is how that distinction gets
    lost, and it is the distinction the whole product rests on.
    """
    return {
        "runId": str(row["id"]),
        "status": str(row["status"]),
        "outcome": str(row["outcome"]),
        "revision": int(row["revision"]),
        "leaseEpoch": int(row["lease_epoch"]),
        "manifestDigest": str(row["manifest_digest"]),
        # Cancellation is metadata rather than a status, so it is reported as metadata. A caller
        # that sees `cancellationRequestedAt` set while status is RUNNING is seeing the truth: the
        # request is recorded and nothing has established that the desktop stopped.
        "cancellationRequestedAt": (
            None if row["cancel_requested_at"] is None else str(row["cancel_requested_at"])
        ),
        "stopAcknowledgedAt": (
            None if row["stop_acknowledged_at"] is None else str(row["stop_acknowledged_at"])
        ),
        "ambiguityReason": row["ambiguity_reason"],
        "quarantined": bool(row["quarantined"]),
        "retryOf": None if row["retry_of"] is None else str(row["retry_of"]),
    }


_RUN_COLUMNS = (
    "id, status, outcome, revision, lease_epoch, manifest_digest, cancel_requested_at, "
    "stop_acknowledged_at, ambiguity_reason, quarantined, retry_of"
)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def request_run(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any], response: Response
) -> dict[str, Any]:
    """Request a run. 202, because requesting is not running.

    The response carries an identity to poll and nothing that looks like a result. A 201 with a run
    body would invite a caller to read a status as an outcome, and at this point there is no outcome
    at all — `NOT_EVALUATED` is what the run actually holds.
    """
    body = as_body(payload)
    context = authorize(
        conn,
        request,
        workspace_id,
        Permission.RUN_REQUEST,
        body,
        frozenset({"manifestDigest", "projectId", "authorizationId", "retryOf"}),
    )

    def perform() -> dict[str, Any]:
        try:
            runners.assert_queue_capacity(conn)
        except runners.QueueFull as exc:
            raise ProblemDetail(
                ProblemCode.QUOTA_EXHAUSTED, str(exc), request_id=context.request_id
            ) from exc
        run_id = runs.create_run(
            conn,
            workspace_id=workspace_id,
            manifest_digest=str(body["manifestDigest"]),
            project_id=body.get("projectId"),
            authorization_id=body.get("authorizationId"),
            retry_of=body.get("retryOf"),
        )
        return {"runId": run_id, "status": "QUEUED", "outcome": "NOT_EVALUATED"}

    try:
        outcome = run_idempotently(conn, context, route="POST /runs", body=body, perform=perform)
    except KeyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, "manifestDigest is required", request_id=context.request_id
        ) from exc

    if outcome.replayed:
        # The stored response, returned only after this request's authority was resolved afresh.
        response.headers["Idempotent-Replay"] = "true"
    result = outcome.response or {}
    response.headers["Location"] = f"/v1/workspaces/{workspace_id}/runs/{result.get('runId')}"
    return result


@router.get("/runs/{run_id}")
def get_run(
    workspace_id: str, run_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    row = conn.execute(f"SELECT {_RUN_COLUMNS} FROM run WHERE id = %s", (run_id,)).fetchone()  # noqa: S608
    if row is None:
        raise not_found()
    view = _run_view(row)
    response.headers["ETag"] = f'"{view["revision"]}"'
    return view


@router.get("/runs")
def list_runs(
    workspace_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    rows = conn.execute(
        f"SELECT {_RUN_COLUMNS} FROM run "  # noqa: S608
        "WHERE (%s::uuid IS NULL OR id > %s::uuid) ORDER BY id LIMIT %s",
        (after, after, size + 1),
    ).fetchall()
    items = [_run_view(r) for r in rows[:size]]
    return {"items": items, "nextCursor": items[-1]["runId"] if len(rows) > size else None}


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def request_cancellation(
    workspace_id: str, run_id: str, request: Request, conn: Conn, payload: dict[str, Any]
) -> dict[str, Any]:
    """Record a cancellation request. Returns request metadata, never a stopped claim.

    The distinction the module prompt insists on, and the response is shaped to make it unmissable:
    `cancellationRequestedAt` is set, `stopAcknowledged` is false, and `meaning` says in words that
    nothing has established the desktop stopped. A 200 with `{"cancelled": true}` would be a lie
    told by a field name.
    """
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.RUN_REQUEST, body, frozenset({"reason"})
    )
    expected = require_if_match(context)

    stored = runs.load_run(conn, run_id=run_id)
    moment = to_rfc3339_utc(datetime.now(UTC))
    try:
        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda s: reducers.request_cancellation(
                s, requested_at=moment, expected_revision=s.revision
            ),
            operation_id=str(uuid.uuid4()),
            topic="run.cancellation_requested",
            expected_revision=expected,
            actor_user=context.principal.user_id,
            audit_action="RUN_CANCELLATION_REQUESTED",
        )
    except runs.StaleRevision as exc:
        raise ProblemDetail(
            ProblemCode.STALE_REVISION, str(exc), request_id=context.request_id
        ) from exc
    except runs.TerminalRun as exc:
        raise ProblemDetail(
            ProblemCode.CONFLICT,
            f"{exc} A terminal record is immutable, so there is nothing left to cancel.",
            request_id=context.request_id,
        ) from exc

    after = runs.load_run(conn, run_id=run_id).state
    return {
        "runId": run_id,
        "status": str(after.status),
        "cancellationRequestedAt": after.cancel_requested_at,
        "stopAcknowledged": after.stop_acknowledged_at is not None,
        "meaning": (
            "Cancellation is requested. No further action will be admitted, and nothing here "
            "establishes that the desktop has stopped: that requires an acknowledgement bound to "
            "the current lease epoch with no action left unresolved. Effects already performed "
            "remain recorded."
        ),
        "previousRevision": stored.state.revision,
    }


@router.get("/runs/{run_id}/events")
def replay_events(
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    request: Request,
    conn: Conn,
    after_sequence: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """Paginated canonical replay.

    `exhausted` is true only for a short page. A full page cannot distinguish "four events exist"
    from "four events exist so far", and reporting exhaustion on one would stop a reader a page
    early whenever the total is a multiple of the page size.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    try:
        page = evidence.replay(
            conn, run_id=run_id, attempt_id=attempt_id, after_sequence=after_sequence, limit=size
        )
    except evidence.FinalizationError as exc:
        raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc), request_id=None) from exc
    return {
        "events": [
            {
                "sequence": int(e["sequence"]),
                "eventId": str(e["event_id"]),
                "eventType": str(e["event_type"]),
                "sourceTime": str(e["source_time"]),
                "previousEventHash": str(e["previous_event_hash"]),
                "payloadDigest": str(e["payload_digest"]),
            }
            for e in page["events"]
        ],
        "nextAfterSequence": page["nextAfterSequence"],
        "exhausted": page["exhausted"],
    }


@router.get("/runs/{run_id}/evidence")
def evidence_summary(
    workspace_id: str, run_id: str, attempt_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """Counts, producer identities and artifact digests. No object keys, no transcript text.

    A summary is the thing most likely to be rendered somewhere unexpected, and an object key in a
    summary is a step towards an object key in a log.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    return evidence.evidence_summary(conn, run_id=run_id, attempt_id=attempt_id)
