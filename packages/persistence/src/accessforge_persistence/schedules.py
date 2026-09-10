"""Bounded schedules over explicitly approved standing authorizations.

A schedule is a recurring request to run work someone already authorized. It cannot broaden that
authorization and it cannot reuse an exact-run approval, and both of those are enforced rather than
documented.

The rule that shapes everything: **an occurrence is admitted once, transactionally.** A unique key
on `(schedule_id, scheduled_for)` means a duplicated timer, two workers racing, and a catch-up
after downtime all resolve to exactly one run. Keying on the instant work *started* would let two
workers a millisecond apart both believe they owned the occurrence.

The second rule: **a missed window is skipped with a reason, never replayed as a burst.** A service
down for six hours does not wake and fire six runs at a desktop that holds one attempt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from accessforge_domain.authority import AuthorityError, ExecutionGrant
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

#: How late an occurrence may be admitted. Beyond this it is skipped with a reason: a run that was
#: due six hours ago tests a source ref and an environment that have both moved on, and admitting it
#: would produce evidence about a state nobody is asking about any more.
MAX_OCCURRENCE_LATENESS = timedelta(minutes=30)

#: One attempt per schedule at a time, by default. A physical desktop holds one attempt (INV-10),
#: so overlapping occurrences would queue behind each other and arrive as a burst, not a schedule.
DEFAULT_MAX_CONCURRENT_OCCURRENCES = 1


class ScheduleError(Exception):
    """A schedule operation was refused."""


class GrantMoved(ScheduleError):
    """The standing authorization changed since this schedule was approved."""


@dataclass(frozen=True, slots=True)
class OccurrenceOutcome:
    occurrence_id: str
    admitted: bool
    run_id: str | None
    reason: str | None


def _now(now: str | None) -> str:
    return now or to_rfc3339_utc(datetime.now(UTC))


def create_schedule(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    name: str,
    grant: ExecutionGrant,
    journey_version_id: str,
    source_ref: str,
    cron_expression: str,
    timezone: str,
    expires_at: str,
    created_by: str,
    now: str | None = None,
) -> str:
    """Create a schedule bound to a grant at a specific revision.

    The journey must already be inside the grant's allowed set. A schedule that named a journey the
    grant does not cover would be a broadening of the authorization dressed as configuration, and it
    would only be discovered at the first occurrence — after someone believed it was approved.
    """
    moment = _now(now)
    grant.check_usable(now=moment)

    if journey_version_id not in grant.allowed_journey_version_ids:
        raise ScheduleError(
            f"journey {journey_version_id} is not in the grant's allowed set. A schedule cannot "
            "broaden the authorization it draws on: that would be a widening of scope dressed as "
            "configuration, discovered at the first occurrence rather than at approval."
        )

    expiry = parse_rfc3339_utc(expires_at, field="expires_at")
    if expiry > parse_rfc3339_utc(grant.expires_at, field="grant.expires_at"):
        raise ScheduleError(
            "a schedule cannot outlive the grant it draws on; it would keep firing under an "
            "authorization that had expired"
        )
    if not timezone.strip():
        raise ScheduleError(
            "a timezone is required. Without one a schedule runs at a different wall-clock hour "
            "twice a year, and 'it fired an hour late in March' is a bug nobody reproduces in July."
        )

    schedule_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO schedule
            (id, workspace_id, name, execution_grant_id, grant_revision, journey_version_id,
             source_ref, cron_expression, timezone, expires_at, created_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            schedule_id,
            workspace_id,
            name,
            grant.grant_id,
            grant.revision,
            journey_version_id,
            source_ref,
            cron_expression,
            timezone,
            expires_at,
            created_by,
            moment,
        ),
    )
    return schedule_id


def pause(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    schedule_id: str,
    actor_id: str,
    now: str | None = None,
) -> None:
    """Stop admitting occurrences, keeping the schedule and who stopped it.

    Paused rather than deleted: deleting loses the record that the schedule existed and that
    somebody turned it off, which is what an operator asking "why did this stop" needs.
    """
    conn.execute(
        "UPDATE schedule SET paused_at = %s, paused_by = %s, revision = revision + 1 "
        "WHERE id = %s AND paused_at IS NULL",
        (_now(now), actor_id, schedule_id),
    )


def resume(conn: psycopg.Connection[dict[str, Any]], *, schedule_id: str) -> None:
    conn.execute(
        "UPDATE schedule SET paused_at = NULL, paused_by = NULL, revision = revision + 1 "
        "WHERE id = %s",
        (schedule_id,),
    )


def admit_occurrence(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    schedule_id: str,
    scheduled_for: str,
    grant: ExecutionGrant,
    create_run: Any,
    now: str | None = None,
    max_lateness: timedelta = MAX_OCCURRENCE_LATENESS,
) -> OccurrenceOutcome:
    """Admit one occurrence, or record why it was not admitted.

    Every refusal produces a row. An occurrence that simply did not happen is indistinguishable from
    one the scheduler never noticed, and the difference is what an operator is trying to establish.

    ``create_run`` is injected rather than imported so this function has no opinion about what a run
    is. It also means the run creation and the occurrence row commit in the caller's transaction: an
    occurrence marked admitted whose run was never created would be an occurrence claiming work that
    does not exist.
    """
    moment = _now(now)
    occurrence_id = str(uuid.uuid4())

    def _skip(reason: str) -> OccurrenceOutcome:
        conn.execute(
            """
            INSERT INTO schedule_occurrence
                (id, workspace_id, schedule_id, scheduled_for, admitted, skipped_reason, created_at)
            VALUES (%s, %s, %s, %s, FALSE, %s, %s)
            ON CONFLICT (schedule_id, scheduled_for) DO NOTHING
            """,
            (occurrence_id, workspace_id, schedule_id, scheduled_for, reason, moment),
        )
        return OccurrenceOutcome(occurrence_id, admitted=False, run_id=None, reason=reason)

    row = conn.execute(
        """
        SELECT execution_grant_id, grant_revision, journey_version_id, paused_at, expires_at
        FROM schedule WHERE id = %s FOR UPDATE
        """,
        (schedule_id,),
    ).fetchone()
    if row is None:
        raise ScheduleError("no such schedule in this workspace")

    if row["paused_at"] is not None:
        return _skip("the schedule is paused")

    if parse_rfc3339_utc(moment, field="now") > row["expires_at"]:
        return _skip("the schedule has expired")

    late_by = parse_rfc3339_utc(moment, field="now") - parse_rfc3339_utc(
        scheduled_for, field="scheduled_for"
    )
    if late_by > max_lateness:
        # The catch-up burst this prevents: a service down for six hours waking up and firing six
        # runs at a desktop that holds one attempt. A run due six hours ago also tests a source ref
        # and an environment that have both moved on.
        return _skip(
            f"the occurrence is {int(late_by.total_seconds() // 60)} minutes late and the limit is "
            f"{int(max_lateness.total_seconds() // 60)}. A late run tests inputs that have since "
            "moved, and admitting a backlog would send a burst at a desktop that holds one attempt."
        )

    # The grant is rechecked here, not trusted from creation time. Between then and now it may have
    # been revoked, revised or expired, and each of those means nobody currently authorizes this.
    if str(row["execution_grant_id"]) != grant.grant_id:
        raise ScheduleError("the supplied grant is not the one this schedule was created against")

    # Read from the row rather than from the object the caller handed us. `grant` is constructed by
    # the caller, so a caller holding one built before a restore would present a grant whose
    # `revalidation_required` is False no matter what the database says -- and the whole property is
    # that a restored grant is unusable until a person clears it in the database.
    restored = conn.execute(
        "SELECT revalidation_required FROM execution_grant WHERE id = %s",
        (grant.grant_id,),
    ).fetchone()
    # A missing row is left alone deliberately. `schedule.execution_grant_id` has no foreign key to
    # `execution_grant`, so a schedule can reference a grant that was never persisted -- which
    # several existing tests do. Refusing here would be a stricter rule than module 19 wrote, and
    # tightening somebody else's contract as a side effect of a restore control is how a change
    # nobody asked for lands in a release. What this check owns is narrow: a grant row that exists
    # and says it needs revalidation is unusable.
    if restored is not None and bool(restored["revalidation_required"]):
        return _skip(
            "the standing authorization was restored from a backup and has not been revalidated. "
            "A grant revoked after the snapshot is live in that data and revoked in the world, and "
            "nothing in it can tell the difference, so it is unusable until a person confirms it."
        )
    try:
        grant.check_usable(now=moment, expected_revision=int(row["grant_revision"]))
    except AuthorityError as exc:
        return _skip(
            f"the standing authorization is no longer usable as approved: {exc}. A schedule does "
            "not run under a grant that moved underneath it."
        )

    if str(row["journey_version_id"]) not in grant.allowed_journey_version_ids:
        return _skip(
            "the grant no longer allows this schedule's journey version; it was narrowed after the "
            "schedule was created"
        )

    active = conn.execute(
        """
        SELECT count(*) AS n FROM schedule_occurrence o
        JOIN run r ON r.id = o.run_id
        WHERE o.schedule_id = %s AND o.admitted
          AND r.status IN ('QUEUED', 'LEASED', 'RUNNING', 'FINALIZING')
        """,
        (schedule_id,),
    ).fetchone()
    if active is not None and int(active["n"]) >= DEFAULT_MAX_CONCURRENT_OCCURRENCES:
        return _skip(
            "an earlier occurrence of this schedule is still running. A physical desktop holds one "
            "attempt, so overlapping occurrences would queue and arrive as a burst rather than a "
            "schedule."
        )

    run_id = create_run()
    inserted = conn.execute(
        """
        INSERT INTO schedule_occurrence
            (id, workspace_id, schedule_id, scheduled_for, run_id, admitted, created_at)
        VALUES (%s, %s, %s, %s, %s, TRUE, %s)
        ON CONFLICT (schedule_id, scheduled_for) DO NOTHING
        RETURNING id
        """,
        (occurrence_id, workspace_id, schedule_id, scheduled_for, run_id, moment),
    ).fetchone()

    if inserted is None:
        # Another worker won the race. The unique key is what decided it, not this code -- and the
        # run created a moment ago is orphaned rather than a duplicate attempt, which the caller's
        # transaction rolls back.
        raise ScheduleError(
            f"occurrence {scheduled_for} was already admitted by another worker; the unique key on "
            "(schedule, scheduled_for) is what makes a duplicated timer produce exactly one run"
        )

    return OccurrenceOutcome(occurrence_id, admitted=True, run_id=run_id, reason=None)


def assert_grant_cannot_authorize(scope: ApprovalScope) -> None:
    """A standing grant authorizes runs and nothing else.

    Stated as a function so a caller reaching for a grant to justify a patch or a publication
    finds a refusal rather than an absence of a check. Scopes do not nest: RUN_EFFECTS is not a
    weaker PATCH_APPLY, it is a different authorization about a different action.
    """
    if scope is not ApprovalScope.RUN_EFFECTS:
        raise ScheduleError(
            f"a standing execution grant cannot authorize {scope}. Scopes do not nest and do not "
            "imply one another: a grant to run a test repeatedly is not permission to change code "
            "or publish anything, however many times it has been used."
        )


def occurrences(
    conn: psycopg.Connection[dict[str, Any]], *, schedule_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT scheduled_for, run_id, admitted, skipped_reason, created_at
            FROM schedule_occurrence WHERE schedule_id = %s
            ORDER BY scheduled_for DESC LIMIT %s
            """,
            (schedule_id, limit),
        ).fetchall()
    ]
