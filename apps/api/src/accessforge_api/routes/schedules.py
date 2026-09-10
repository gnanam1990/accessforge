"""Recurring runs under a standing grant.

A schedule requests runs. It does not run anything, it does not carry authority of its own, and it
cannot widen the grant it draws on — which is why creating one requires the grant, at the revision
the caller has read, rather than just its id.

The behaviour worth understanding before using these routes: **every occurrence is rechecked against
the grant as it stands at that moment.** A grant that has been revoked, revised, narrowed, expired,
or marked as requiring revalidation after a restore stops the schedule without anybody editing it,
and the occurrence records why it was skipped. That is the design working, not a fault — but it
means a schedule that quietly stops firing is a question to ask of the grant, so `GET
/schedules/{id}/occurrences` reports the skip reason rather than an empty list.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size, require_if_match, run_idempotently
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, as_identifier, authorize, workspace_scope
from accessforge_domain.authority import AuthorityError
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import grants, schedules

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["schedules"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]

CREATE_FIELDS = frozenset(
    {
        "name",
        "grantId",
        "journeyVersionId",
        "sourceRef",
        "cronExpression",
        "timezone",
        "expiresAt",
    }
)


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any], response: Response
) -> dict[str, Any]:
    """Bind a schedule to a grant at a specific revision.

    The grant is loaded here and passed to the domain rather than trusted from the body. A caller
    supplying grant fields would be describing an authorization instead of drawing on one, and the
    description is the part an attacker controls.
    """
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.RUN_APPROVE, body=body, allowed_fields=CREATE_FIELDS
    )

    missing = sorted(CREATE_FIELDS - set(body))
    if missing:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            f"missing required field(s): {', '.join(missing)}",
            request_id=context.request_id,
        )

    def perform() -> dict[str, Any]:
        try:
            stored_grant = grants.load_grant(
                conn, grant_id=as_identifier(str(body["grantId"]), what="grantId")
            )
        except grants.NoSuchGrant:
            raise not_found() from None

        try:
            schedule_id = schedules.create_schedule(
                conn,
                workspace_id=workspace_id,
                name=str(body["name"]),
                grant=stored_grant.grant,
                journey_version_id=as_identifier(
                    str(body["journeyVersionId"]), what="journeyVersionId"
                ),
                source_ref=str(body["sourceRef"]),
                cron_expression=str(body["cronExpression"]),
                timezone=str(body["timezone"]),
                expires_at=str(body["expiresAt"]),
                created_by=context.principal.user_id,
            )
        except AuthorityError as exc:
            # The grant itself is unusable — revoked, expired, or awaiting revalidation after a
            # restore. A 409 rather than a 400: the body is fine and the state is not.
            raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc
        except schedules.ScheduleError as exc:
            raise ProblemDetail(ProblemCode.INVALID_INPUT, str(exc)) from exc
        return schedules.load_schedule(conn, schedule_id=schedule_id).as_dict()

    outcome = run_idempotently(conn, context, route="POST /schedules", body=body, perform=perform)
    result = outcome.response or {}
    response.headers["ETag"] = f'"{result.get("revision", 1)}"'
    return result


@router.get("/schedules")
def list_schedules(
    workspace_id: str, request: Request, conn: Conn, limit: int | None = None
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    found = schedules.list_schedules(conn, workspace_id=workspace_id, limit=clamp_page_size(limit))
    return {
        "items": [s.as_dict() for s in found],
        "meaning": (
            "Paused schedules are listed. 'Why did this stop firing' is the question these answer, "
            "and hiding a paused one would make it look deleted."
        ),
    }


@router.get("/schedules/{schedule_id}")
def read_schedule(
    workspace_id: str, schedule_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        stored = schedules.load_schedule(
            conn, schedule_id=as_identifier(schedule_id, what="scheduleId")
        )
    except schedules.ScheduleError:
        raise not_found() from None
    response.headers["ETag"] = f'"{stored.revision}"'
    return stored.as_dict()


@router.get("/schedules/{schedule_id}/occurrences")
def read_occurrences(
    workspace_id: str,
    schedule_id: str,
    request: Request,
    conn: Conn,
    limit: int | None = None,
) -> dict[str, Any]:
    """What this schedule actually did, including everything it declined to do.

    A skipped occurrence is a row with a reason, not an absence. That is the whole point: a schedule
    that stopped firing because its grant was revoked looks identical to one nobody configured,
    unless the skip is recorded.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    try:
        stored = schedules.load_schedule(
            conn, schedule_id=as_identifier(schedule_id, what="scheduleId")
        )
    except schedules.ScheduleError:
        raise not_found() from None

    rows = schedules.occurrences(conn, schedule_id=stored.schedule_id, limit=clamp_page_size(limit))
    return {
        "scheduleId": stored.schedule_id,
        "items": [
            {
                "scheduledFor": str(r["scheduled_for"]),
                "runId": str(r["run_id"]) if r["run_id"] else None,
                "admitted": bool(r["admitted"]),
                "skippedReason": r["skipped_reason"],
                "createdAt": str(r["created_at"]),
            }
            for r in rows
        ],
        "meaning": (
            "An occurrence that was skipped is recorded with its reason rather than omitted. A "
            "schedule silenced by a revoked or unrevalidated grant would otherwise be "
            "indistinguishable from one that never existed."
        ),
    }


@router.post("/schedules/{schedule_id}/pause")
def pause_schedule(
    workspace_id: str, schedule_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Stop admitting occurrences, keeping the schedule and who stopped it.

    Paused rather than deleted. Deleting loses the record that the schedule existed and that
    somebody turned it off, which is exactly what the next operator asking "why did this stop"
    needs.
    """
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body={})
    revision = require_if_match(context)
    stored = _load_at(conn, schedule_id, revision)
    schedules.pause(conn, schedule_id=stored.schedule_id, actor_id=context.principal.user_id)
    updated = schedules.load_schedule(conn, schedule_id=stored.schedule_id)
    response.headers["ETag"] = f'"{updated.revision}"'
    return updated.as_dict()


@router.post("/schedules/{schedule_id}/resume")
def resume_schedule(
    workspace_id: str, schedule_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Start admitting occurrences again — which is not the same as making them run.

    Resuming does not revalidate the grant. The next occurrence is rechecked as every occurrence is,
    so a schedule resumed under a revoked or unrevalidated grant will still skip, with a reason.
    """
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body={})
    revision = require_if_match(context)
    stored = _load_at(conn, schedule_id, revision)
    schedules.resume(conn, schedule_id=stored.schedule_id)
    updated = schedules.load_schedule(conn, schedule_id=stored.schedule_id)
    response.headers["ETag"] = f'"{updated.revision}"'
    return updated.as_dict()


def _load_at(
    conn: psycopg.Connection[Any], schedule_id: str, revision: int
) -> schedules.StoredSchedule:
    """Load a schedule and refuse if it moved since the caller read it.

    Pausing a schedule somebody else has just repointed at a different journey is a decision about
    state that no longer exists, and the pause would look like it had worked.
    """
    try:
        stored = schedules.load_schedule(
            conn, schedule_id=as_identifier(schedule_id, what="scheduleId")
        )
    except schedules.ScheduleError:
        raise not_found() from None
    if stored.revision != revision:
        raise ProblemDetail(
            ProblemCode.STALE_REVISION,
            f"this schedule is at revision {stored.revision} and you supplied {revision}; it "
            "changed since you read it",
        )
    return stored


@router.post("/schedules/{schedule_id}/reapprove")
def reapprove_schedule(
    workspace_id: str, schedule_id: str, request: Request, conn: Conn, response: Response
) -> dict[str, Any]:
    """Re-bind a schedule to its grant as the grant now stands.

    A schedule records the grant revision it was approved against, so a grant that has moved stops
    it. After a restore the grant moves twice — reconciliation marks it as needing revalidation, and
    a person clearing that moves it again — which leaves every schedule stopped even once the grant
    is confirmed. This is the way out, and without it that state is a dead end.

    Deliberately *not* part of revalidating the grant. "This standing authorization is still valid"
    and "this particular recurring job should start running again" are two decisions, and a system
    that made the second follow from the first would restart work nobody asked it to restart —
    possibly at 2am, against a desktop, on the strength of an operator clicking one confirmation
    during an incident.

    Resuming a paused schedule is included, because a schedule paused *by* the restore should not
    need a second click. The grant's own checks are re-run: a grant narrowed since is refused rather
    than re-bound.
    """
    context = authorize(conn, request, workspace_id, Permission.RUN_APPROVE, body={})
    revision = require_if_match(context)
    stored = _load_at(conn, schedule_id, revision)

    try:
        stored_grant = grants.load_grant(conn, grant_id=stored.grant_id)
    except grants.NoSuchGrant:
        raise not_found() from None

    try:
        schedules.rebind_to_grant(
            conn,
            schedule_id=stored.schedule_id,
            grant=stored_grant.grant,
            actor_id=context.principal.user_id,
        )
    except AuthorityError as exc:
        raise ProblemDetail(
            ProblemCode.CONFLICT,
            f"{exc} Re-approving a schedule cannot make its grant usable; confirm the grant first.",
        ) from exc
    except schedules.ScheduleError as exc:
        raise ProblemDetail(ProblemCode.CONFLICT, str(exc)) from exc

    updated = schedules.load_schedule(conn, schedule_id=stored.schedule_id)
    response.headers["ETag"] = f'"{updated.revision}"'
    return {
        **updated.as_dict(),
        "confirmed": (
            "This schedule is re-bound to its grant at the grant's current revision and will admit "
            "occurrences again. That is a statement about authorization, not about capacity: an "
            "occurrence still needs a runner, and one with no desktop available is still skipped."
        ),
    }
