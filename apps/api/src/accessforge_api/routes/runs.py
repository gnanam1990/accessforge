"""Runs: request, inspect, cancel, retry, and evidence replay."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain import reducers
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import evidence, runners, runs
from accessforge_persistence.evidence import artifacts as artifacts_module

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


@router.get("/runs/{run_id}/attempts")
def list_attempts(workspace_id: str, run_id: str, request: Request, conn: Conn) -> dict[str, Any]:
    """The attempts made on one run, newest epoch first.

    Every other evidence route is scoped to an attempt, and until now a caller had to already know
    an attempt id to use them. A run can have more than one attempt only in the sense that a lease
    was granted more than once; each has its own canonical chain, and mixing two attempts' events
    into one timeline would produce a sequence that never happened.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(run_id, what="the run")
    exists = conn.execute("SELECT 1 FROM run WHERE id = %s", (run_id,)).fetchone()
    if exists is None:
        raise not_found()
    rows = conn.execute(
        """
        SELECT id, lease_epoch, started_at, ended_at
        FROM run_attempt WHERE run_id = %s ORDER BY lease_epoch DESC
        """,
        (run_id,),
    ).fetchall()
    return {
        "items": [
            {
                "attemptId": str(r["id"]),
                "leaseEpoch": int(r["lease_epoch"]),
                "startedAt": str(r["started_at"]),
                # Null means the attempt has no recorded end. Not "still running": an attempt whose
                # runner vanished also has no end, and the two are told apart by the run's status
                # and its ambiguity reason, not by this field.
                "endedAt": None if r["ended_at"] is None else str(r["ended_at"]),
            }
            for r in rows
        ]
    }


@router.get("/runs/{run_id}/timeline")
def replay_timeline(
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    request: Request,
    conn: Conn,
    after_sequence: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    """The canonical chain with provenance, for a reader rather than a verifier.

    Separate from `/events`, which serves digests only. A person inspecting a run needs to know
    *who* said each thing and *what they said*, and the two questions have different safety
    properties — so they have different routes and this one carries the extra care.

    Three rules hold here.

    **Ordering is the sequencer's, never a clock.** `sequence` is assigned by the trusted sequencer
    inside the transaction that admits a record. Producers submit source times from their own
    machines, and those disagree; sorting by them would reorder an attempt according to whose clock
    was fast.

    **Provenance travels with every event.** `producerId` says which identity submitted it and
    `producerSequence` says where that producer thought it sat. A supervisor's receipt is not
    independent observer proof, and a timeline that rendered both as "evidence" would erase the
    distinction the whole outcome depends on.

    The producer's *role* is not a stored column — module 10 records a producer identifier and
    nothing typed alongside it — so this route reports the identifier and the screen says that the
    role is a convention rather than a field. Making it a field is a schema change to a module this
    one does not own, and inventing a role here by parsing the identifier would be exactly the kind
    of derived authority this product refuses everywhere else.

    **The payload is the recorded one.** Not a summary, not a narration. Where a payload carries
    fixture input it was already redacted at ingestion; nothing is redacted here, because a
    redaction applied at read time is one that can be forgotten at the next read.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(run_id, what="the run")
    as_identifier(attempt_id, what="the attempt")
    size = clamp_page_size(limit)
    rows = conn.execute(
        """
        SELECT e.sequence, e.event_id, e.event_type, e.source_time, e.received_time,
               e.lease_epoch, e.payload_digest, e.payload, e.previous_event_hash,
               r.producer_id, r.producer_sequence, r.source_record_digest
          FROM canonical_event e
          LEFT JOIN producer_source_record r
                 ON r.canonical_sequence = e.sequence AND r.attempt_id = e.attempt_id
         WHERE e.run_id = %s AND e.attempt_id = %s AND e.sequence > %s
         ORDER BY e.sequence
         LIMIT %s
        """,
        (run_id, attempt_id, after_sequence, size + 1),
    ).fetchall()

    events = [
        {
            "sequence": int(r["sequence"]),
            "eventId": str(r["event_id"]),
            "eventType": str(r["event_type"]),
            "leaseEpoch": int(r["lease_epoch"]),
            # Kept as context, and never used for ordering. Two producers' clocks disagree, and a
            # reader is entitled to see that they do.
            "sourceTime": str(r["source_time"]),
            "receivedTime": str(r["received_time"]),
            "payloadDigest": str(r["payload_digest"]),
            "previousEventHash": str(r["previous_event_hash"]),
            "payload": r["payload"],
            "producerId": None if r["producer_id"] is None else str(r["producer_id"]),
            "producerSequence": (
                None if r["producer_sequence"] is None else int(r["producer_sequence"])
            ),
            "sourceRecordDigest": (
                None if r["source_record_digest"] is None else str(r["source_record_digest"])
            ),
        }
        for r in rows[:size]
    ]
    return {
        "events": events,
        "nextAfterSequence": events[-1]["sequence"] if events else after_sequence,
        # True only for a short page. A full page cannot tell "this is all of them" from "this is
        # all of them so far", and claiming exhaustion on a full page stops a reader one page early
        # whenever the total is a multiple of the page size.
        "exhausted": len(rows) <= size,
        "orderingMeaning": (
            "Ordered by the sequence the trusted sequencer assigned, not by any clock. Source "
            "times come from the producers' own machines and disagree with each other."
        ),
    }


@router.get("/runs/{run_id}/completeness")
def evidence_completeness(
    workspace_id: str, run_id: str, attempt_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """What is missing from this attempt's evidence, and nothing about what it means.

    There is deliberately no outcome in this response. Completeness is an input to a verdict, not a
    verdict: a complete evidence set can still describe a failure, and an incomplete one does not
    become a pass by being tidy. The reasons are the product.

    `contiguous` and `producersClosed` are separate fields because they are separate properties.
    A contiguous chain proves no record is missing from the *middle*; it says nothing about a
    producer that stopped halfway and left a perfect chain covering half the attempt (INV-06).
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    as_identifier(run_id, what="the run")
    as_identifier(attempt_id, what="the attempt")
    from accessforge_persistence import sequencer

    contiguous = sequencer.chain_is_contiguous(conn, run_id=run_id, attempt_id=attempt_id)
    streams = conn.execute(
        """
        SELECT producer_id, admitted_through, closed_at_sequence
        FROM producer_stream WHERE attempt_id = %s ORDER BY producer_id
        """,
        (attempt_id,),
    ).fetchall()
    unclosed = [str(r["producer_id"]) for r in streams if r["closed_at_sequence"] is None]
    missing = artifacts_module.missing_required_artifacts(
        conn, run_id=run_id, attempt_id=attempt_id
    )
    lifecycle = conn.execute(
        """
        SELECT
          bool_or(event_type = 'RUN_STARTED')  AS started,
          bool_or(event_type = 'RUN_FINISHED') AS finished
        FROM canonical_event WHERE run_id = %s AND attempt_id = %s
        """,
        (run_id, attempt_id),
    ).fetchone()
    bounded = bool(lifecycle and lifecycle["started"] and lifecycle["finished"])

    reasons: list[str] = []
    if not contiguous:
        reasons.append(
            "the canonical event chain has gaps: records were admitted at non-consecutive "
            "positions, so events are missing from the middle of this attempt"
        )
    if unclosed:
        reasons.append(
            f"required producers have not closed their streams: {', '.join(unclosed)}. A "
            "contiguous chain does not cover this — a producer that stopped halfway leaves a "
            "perfect chain and half the evidence."
        )
    if missing:
        reasons.append(
            "required artifacts are missing: "
            + ", ".join(f"{m['kind']} from {m['producer']} ({m['reason']})" for m in missing)
        )
    if not bounded:
        reasons.append(
            "the attempt has no RUN_STARTED and RUN_FINISHED pair, so its extent is undefined and "
            "nothing establishes that the evidence covers the whole of it"
        )

    return {
        "reasons": reasons,
        "contiguous": contiguous,
        "producersClosed": not unclosed,
        "artifactsPresent": not missing,
        "lifecycleBounded": bounded,
        "producers": [
            {
                "producerId": str(r["producer_id"]),
                "admittedThrough": int(r["admitted_through"]),
                "closedAt": (
                    None if r["closed_at_sequence"] is None else int(r["closed_at_sequence"])
                ),
            }
            for r in streams
        ],
        "meaning": (
            "This describes the evidence, not the run. A complete evidence set can still describe "
            "a failure, and an incomplete one does not become a pass by being tidy."
        ),
    }
