"""Event replay across reconnects, and schedules that cannot broaden their authorization.

The crash cases the prompt lists: outbox publication interrupted, a duplicated occurrence, a worker
dying mid-work, a parent grant revoked between creation and dispatch, mutated inputs, and a
grant used to justify a patch. Plus the SSE cases: an old cursor, a foreign cursor, and a reconnect
that must not imply completion.

Requirements: FR-014, FR-015, FR-019, FR-021. Invariants: INV-06, INV-07, INV-08, INV-11, INV-13.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from accessforge_domain import reducers
from accessforge_domain.authority import ExecutionGrant
from accessforge_domain.canonical import digest
from accessforge_domain.states import ApprovalScope
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import (
    assert_row_level_security_enforced,
    events,
    migrate,
    outbox,
    projects,
    runs,
    schedules,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x190))
WS_OTHER = str(uuid.UUID(int=0x191))
ACTOR = str(uuid.UUID(int=0x192))
GRANT_ID = str(uuid.UUID(int=0x193))
MANIFEST = digest({"m": "19"})
NOW = "2026-09-10T12:00:00.000000Z"


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _journey(url: str, workspace: str = WS) -> str:
    with workspace_connection(url, workspace) as conn:
        project_id = projects.create_project(conn, workspace_id=workspace, name="p")
        version_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO journey_version
                (id, workspace_id, project_id, name, platform, journey_digest,
                 assertion_set_digest, fixture_digest, navigator_policy_digest, navigator_policy,
                 reviewer_summary)
            VALUES (%s, %s, %s, 'j', 'darwin', %s, %s, %s, %s, '{}', '{}')
            """,
            (version_id, workspace, project_id, MANIFEST, MANIFEST, MANIFEST, MANIFEST),
        )
    return version_id


def _publish(url: str, topic: str, workspace: str = WS) -> int:
    """Enqueue an outbox message and mark it published, as the publisher worker would."""
    with workspace_connection(url, workspace) as conn:
        outbox.enqueue_message(
            conn,
            workspace_id=workspace,
            operation_id=str(uuid.uuid4()),
            topic=topic,
            reference={"note": topic},
        )
    with workspace_connection(url, workspace) as conn:
        claimed = outbox.claim_messages(conn, claimed_by="publisher-1", limit=10)
        for message in claimed:
            outbox.mark_published(conn, message_id=message.id)
        return claimed[-1].id if claimed else 0


def _grant(**overrides: object) -> ExecutionGrant:
    fields: dict[str, object] = {
        "grant_id": GRANT_ID,
        "workspace_id": WS,
        "project_id": str(uuid.uuid4()),
        "environment": "local",
        "allowed_journey_version_ids": frozenset(),
        "allowed_policy_version_ids": frozenset({"policy-1"}),
        "permitted_effects": frozenset({"CREATE_TEST_REQUEST"}),
        "action_budget": 100,
        "wall_time_budget_seconds": 900,
        "expires_at": "2026-09-30T12:00:00.000000Z",
        "revision": 3,
    }
    fields.update(overrides)
    return ExecutionGrant(**fields)  # type: ignore[arg-type]


# --- the event cursor ----------------------------------------------------------------------------


def test_events_are_read_in_commit_order_after_a_cursor(db: str) -> None:
    first = _publish(db, "run.queued")
    second = _publish(db, "run.leased")
    with workspace_connection(db, WS) as conn:
        after_first = events.read_events(conn, workspace_id=WS, after_event_id=first)
    assert [e.event_id for e in after_first] == [second]


def test_the_cursor_is_an_integer_rather_than_a_timestamp(db: str) -> None:
    """Two events committed in the same microsecond are indistinguishable by time.

    A timestamp cursor would skip one of them and keep skipping for the rest of the connection, with
    nothing to notice. Asserted structurally: the cursor parameter is an int and the id is an int.
    """
    import inspect

    # The annotation is a string because of `from __future__ import annotations`; comparing it to
    # the `int` object was the first version of this assertion and failed for that reason alone.
    signature = inspect.signature(events.read_events)
    assert signature.parameters["after_event_id"].annotation == "int"
    _publish(db, "run.queued")
    with workspace_connection(db, WS) as conn:
        streamed = events.read_events(conn, workspace_id=WS)
    assert isinstance(streamed[0].event_id, int)


def test_an_unpublished_event_is_not_streamed(db: str) -> None:
    """The outbox row exists from the moment the state changed; it is streamed once published.

    Streaming an unpublished row would deliver an event the publisher had not yet committed to
    delivering, and a consumer acting on it could outrun the work it announces.
    """
    with workspace_connection(db, WS) as conn:
        outbox.enqueue_message(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            topic="run.queued",
            reference={},
        )
    with workspace_connection(db, WS) as conn:
        assert events.read_events(conn, workspace_id=WS) == []


def test_an_sse_frame_carries_the_cursor_as_its_id(db: str) -> None:
    """`id:` is what a browser echoes back in `Last-Event-ID` without the application doing
    anything. That is why this transport was chosen: the resume contract is in the protocol."""
    event_id = _publish(db, "run.queued")
    with workspace_connection(db, WS) as conn:
        frame = events.read_events(conn, workspace_id=WS)[0].as_sse()
    assert frame.startswith(f"id: {event_id}\n")
    assert "event: run.queued" in frame
    assert frame.endswith("\n\n")


# --- replay gaps ---------------------------------------------------------------------------------


def test_a_cursor_below_the_retention_floor_is_a_reset(db: str) -> None:
    """Not a short page. A client served what remained would believe it had seen everything since
    its cursor, which is the one thing a resume must never get wrong."""
    _publish(db, "run.queued")
    latest = _publish(db, "run.leased")
    with workspace_connection(db, WS) as conn:
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=latest)
        with pytest.raises(events.ReplayGap, match="resynchronise from the snapshot"):
            events.read_events(conn, workspace_id=WS, after_event_id=latest - 1)


def test_a_cursor_exactly_at_the_floor_is_accepted(db: str) -> None:
    """The boundary. A client holding the floor event has seen it and wants what came after;
    rejecting that would turn every correct resume at the edge into a spurious reset."""
    first = _publish(db, "run.queued")
    second = _publish(db, "run.leased")
    with workspace_connection(db, WS) as conn:
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=first)
        streamed = events.read_events(conn, workspace_id=WS, after_event_id=first)
    assert [e.event_id for e in streamed] == [second]


def test_the_retention_floor_never_moves_backwards(db: str) -> None:
    """A late worker writing a lower floor would re-open a window that had already been declared
    closed, and a client would resume into events that may have been purged."""
    with workspace_connection(db, WS) as conn:
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=100)
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=50)
        assert events.retained_from(conn, workspace_id=WS) == 100


# --- reconnection never implies completion -------------------------------------------------------


def test_a_snapshot_carries_the_cursor_it_was_taken_at(db: str) -> None:
    """A snapshot without a cursor is a race with no safe resume: events already reflected in it
    would be applied twice, or events after it missed, depending on timing nobody controls."""
    with workspace_connection(db, WS) as conn:
        runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
    event_id = _publish(db, "run.queued")
    with workspace_connection(db, WS) as conn:
        taken = events.snapshot(conn, workspace_id=WS)
    assert taken.as_of_event_id == event_id
    assert len(taken.runs) == 1


def test_an_empty_stream_says_nothing_about_completion(db: str) -> None:
    """The failure this guards: a client reconnects, receives no further events, and concludes the
    run finished. The snapshot is the only thing that answers what is true now, and it reports the
    run still QUEUED."""
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
    event_id = _publish(db, "run.queued")

    with workspace_connection(db, WS) as conn:
        assert events.read_events(conn, workspace_id=WS, after_event_id=event_id) == []
        taken = events.snapshot(conn, workspace_id=WS)

    run = next(r for r in taken.runs if r["runId"] == run_id)
    assert run["status"] == "QUEUED"
    assert run["outcome"] == "NOT_EVALUATED"


# --- consumer progress ---------------------------------------------------------------------------


def test_progress_is_durable_and_never_rewinds(db: str) -> None:
    """A stale worker writing after a newer one would otherwise re-deliver everything in between:
    safe for a fully idempotent consumer, wasteful for all of them, and unsafe for one that is only
    nearly idempotent."""
    with workspace_connection(db, WS) as conn:
        events.record_progress(conn, workspace_id=WS, consumer_id="ui", processed_through=10)
        events.record_progress(conn, workspace_id=WS, consumer_id="ui", processed_through=4)
        assert events.progress_of(conn, workspace_id=WS, consumer_id="ui") == 10


def test_a_publisher_restart_redelivers_rather_than_skips(db: str) -> None:
    """Progress is advanced after the work is durable, never before.

    Simulated by not recording progress for the second event: a restarted consumer sees it again.
    At-least-once is what consumers are built for; losing a committed event is not recoverable.
    """
    first = _publish(db, "run.queued")
    second = _publish(db, "run.leased")
    with workspace_connection(db, WS) as conn:
        events.record_progress(conn, workspace_id=WS, consumer_id="ui", processed_through=first)

    with workspace_connection(db, WS) as conn:
        resume_from = events.progress_of(conn, workspace_id=WS, consumer_id="ui")
        redelivered = events.read_events(conn, workspace_id=WS, after_event_id=resume_from)
    assert [e.event_id for e in redelivered] == [second]


# --- tenancy -------------------------------------------------------------------------------------


def test_another_tenants_events_are_not_visible(db: str) -> None:
    """A foreign cursor cannot reach another workspace's history: the connection is scoped, so the
    query returns nothing rather than someone else's payloads."""
    _publish(db, "run.queued", workspace=WS_OTHER)
    with workspace_connection(db, WS) as conn:
        assert events.read_events(conn, workspace_id=WS) == []


def test_stream_positions_and_retention_are_workspace_isolated(db: str) -> None:
    with workspace_connection(db, WS) as conn:
        events.record_progress(conn, workspace_id=WS, consumer_id="ui", processed_through=7)
        events.set_retention_floor(conn, workspace_id=WS, floor_event_id=7)
    with workspace_connection(db, WS_OTHER) as conn:
        assert events.progress_of(conn, workspace_id=WS_OTHER, consumer_id="ui") == 0
        assert events.retained_from(conn, workspace_id=WS_OTHER) == 0


# --- schedules: scope ----------------------------------------------------------------------------


def test_a_schedule_cannot_name_a_journey_outside_its_grant(db: str) -> None:
    """A widening of scope dressed as configuration, and it would only surface at the first
    occurrence -- after somebody believed it was approved."""
    journey = _journey(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(schedules.ScheduleError, match="cannot broaden"):
            schedules.create_schedule(
                conn,
                workspace_id=WS,
                name="nightly",
                grant=_grant(allowed_journey_version_ids=frozenset({"some-other-journey"})),
                journey_version_id=journey,
                source_ref="refs/heads/main",
                cron_expression="0 2 * * *",
                timezone="Europe/London",
                expires_at="2026-09-20T12:00:00.000000Z",
                created_by=ACTOR,
                now=NOW,
            )


def test_a_schedule_cannot_outlive_its_grant(db: str) -> None:
    journey = _journey(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(schedules.ScheduleError, match="cannot outlive"):
            schedules.create_schedule(
                conn,
                workspace_id=WS,
                name="nightly",
                grant=_grant(allowed_journey_version_ids=frozenset({journey})),
                journey_version_id=journey,
                source_ref="refs/heads/main",
                cron_expression="0 2 * * *",
                timezone="Europe/London",
                expires_at="2027-01-01T00:00:00.000000Z",
                created_by=ACTOR,
                now=NOW,
            )


def test_a_timezone_is_required(db: str) -> None:
    """Without one a schedule runs at a different wall-clock hour twice a year."""
    journey = _journey(db)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(schedules.ScheduleError, match="timezone is required"):
            schedules.create_schedule(
                conn,
                workspace_id=WS,
                name="nightly",
                grant=_grant(allowed_journey_version_ids=frozenset({journey})),
                journey_version_id=journey,
                source_ref="refs/heads/main",
                cron_expression="0 2 * * *",
                timezone="  ",
                expires_at="2026-09-20T12:00:00.000000Z",
                created_by=ACTOR,
                now=NOW,
            )


def test_a_grant_cannot_authorize_a_patch_or_a_publication(db: str) -> None:
    """Scopes do not nest. A grant to run a test repeatedly is not permission to change code,
    however many times it has been used."""
    for scope in (ApprovalScope.PATCH_APPLY, ApprovalScope.GITHUB_PUBLISH):
        with pytest.raises(schedules.ScheduleError, match="cannot authorize"):
            schedules.assert_grant_cannot_authorize(scope)
    schedules.assert_grant_cannot_authorize(ApprovalScope.RUN_EFFECTS)


# --- schedules: occurrences ----------------------------------------------------------------------


def _schedule(db: str, journey: str) -> str:
    with workspace_connection(db, WS) as conn:
        return schedules.create_schedule(
            conn,
            workspace_id=WS,
            name="nightly",
            grant=_grant(allowed_journey_version_ids=frozenset({journey})),
            journey_version_id=journey,
            source_ref="refs/heads/main",
            cron_expression="0 2 * * *",
            timezone="Europe/London",
            expires_at="2026-09-20T12:00:00.000000Z",
            created_by=ACTOR,
            now=NOW,
        )


def _admit(
    db: str,
    schedule_id: str,
    journey: str,
    *,
    scheduled_for: str = NOW,
    now: str = NOW,
    grant: ExecutionGrant | None = None,
) -> schedules.OccurrenceOutcome:
    with workspace_connection(db, WS) as conn:

        def create_run() -> str:
            return runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)

        return schedules.admit_occurrence(
            conn,
            workspace_id=WS,
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            grant=grant or _grant(allowed_journey_version_ids=frozenset({journey})),
            create_run=create_run,
            now=now,
        )


def test_an_occurrence_produces_one_run(db: str) -> None:
    """The control. Every refusal below is meaningless against a scheduler that admits none."""
    journey = _journey(db)
    outcome = _admit(db, _schedule(db, journey), journey)
    assert outcome.admitted
    assert outcome.run_id is not None


def test_a_duplicated_occurrence_produces_exactly_one_run(db: str) -> None:
    """Keyed on the scheduled instant rather than on when work started: two workers a millisecond
    apart would both believe they owned the occurrence.

    The first run is terminalized before the retry, because otherwise the overlap check refuses it
    first -- which is also correct, and is covered by its own test. This one is specifically about
    the unique key on (schedule, scheduled_for), which is what decides a race the checks above do
    not see.
    """
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    first = _admit(db, schedule_id, journey)
    assert first.run_id is not None

    with workspace_connection(db, WS) as conn:
        state = runs.load_run(conn, run_id=first.run_id).state
        runs.apply_transition(
            conn,
            run_id=first.run_id,
            reducer=lambda s: reducers.request_cancellation(
                s, requested_at=NOW, expected_revision=s.revision
            ),
            operation_id=str(uuid.uuid4()),
            topic="run.cancellation_requested",
            expected_revision=state.revision,
            actor_service="test",
        )
        state = runs.load_run(conn, run_id=first.run_id).state
        runs.apply_transition(
            conn,
            run_id=first.run_id,
            reducer=lambda s: reducers.cancel(s, expected_revision=s.revision),
            operation_id=str(uuid.uuid4()),
            topic="run.cancelled",
            expected_revision=state.revision,
            actor_service="test",
        )

    with pytest.raises(schedules.ScheduleError, match="already admitted"):
        _admit(db, schedule_id, journey)

    with workspace_connection(db, WS) as conn:
        rows = schedules.occurrences(conn, schedule_id=schedule_id)
    assert len(rows) == 1
    assert str(rows[0]["run_id"]) == first.run_id


def test_a_late_occurrence_is_skipped_with_a_reason_rather_than_replayed(db: str) -> None:
    """The catch-up burst this prevents: a service down for six hours waking and firing six runs at
    a desktop that holds one attempt. A late run also tests inputs that have since moved."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    much_later = to_rfc3339_utc(datetime(2026, 9, 10, 12, 0, tzinfo=UTC) + timedelta(hours=6))
    outcome = _admit(db, schedule_id, journey, scheduled_for=NOW, now=much_later)
    assert not outcome.admitted
    assert outcome.run_id is None
    assert "late" in (outcome.reason or "")

    with workspace_connection(db, WS) as conn:
        rows = schedules.occurrences(conn, schedule_id=schedule_id)
    assert rows[0]["skipped_reason"], "a skipped occurrence says why, rather than leaving no trace"


def test_a_revoked_grant_skips_the_occurrence(db: str) -> None:
    """Rechecked at the occurrence, not trusted from creation. Between then and now the grant may
    have been revoked, revised or expired, and each means nobody currently authorizes this."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    outcome = _admit(
        db,
        schedule_id,
        journey,
        grant=_grant(allowed_journey_version_ids=frozenset({journey}), revoked=True),
    )
    assert not outcome.admitted
    assert "no longer usable" in (outcome.reason or "")


def test_a_revised_grant_skips_the_occurrence(db: str) -> None:
    """A grant revised since approval is a different decision; the schedule pins the revision."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    outcome = _admit(
        db,
        schedule_id,
        journey,
        grant=_grant(allowed_journey_version_ids=frozenset({journey}), revision=4),
    )
    assert not outcome.admitted
    assert "no longer usable" in (outcome.reason or "")


def test_a_narrowed_grant_skips_the_occurrence(db: str) -> None:
    """The grant still usable, the journey removed from it after the schedule was created."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    outcome = _admit(
        db,
        schedule_id,
        journey,
        grant=_grant(allowed_journey_version_ids=frozenset({"something-else"})),
    )
    assert not outcome.admitted
    assert "no longer allows" in (outcome.reason or "")


def test_a_paused_schedule_admits_nothing(db: str) -> None:
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    with workspace_connection(db, WS) as conn:
        schedules.pause(conn, schedule_id=schedule_id, actor_id=ACTOR, expected_revision=1)
    outcome = _admit(db, schedule_id, journey)
    assert not outcome.admitted
    assert outcome.reason == "the schedule is paused"


def test_pausing_keeps_the_schedule_and_who_paused_it(db: str) -> None:
    """Deleting loses the record that the schedule existed and that somebody turned it off."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    with workspace_connection(db, WS) as conn:
        schedules.pause(conn, schedule_id=schedule_id, actor_id=ACTOR, expected_revision=1)
        row = conn.execute(
            "SELECT paused_at, paused_by FROM schedule WHERE id = %s", (schedule_id,)
        ).fetchone()
    assert row is not None
    assert row["paused_at"] is not None
    assert str(row["paused_by"]) == ACTOR


def test_a_resumed_schedule_admits_again(db: str) -> None:
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    with workspace_connection(db, WS) as conn:
        schedules.pause(conn, schedule_id=schedule_id, actor_id=ACTOR, expected_revision=1)
        # Revision 2 after the pause: every schedule mutation now confirms the revision while
        # holding the row lock, so a caller has to say which state it is acting on.
        schedules.resume(conn, schedule_id=schedule_id, expected_revision=2)
    assert _admit(db, schedule_id, journey).admitted


def test_an_overlapping_occurrence_is_skipped(db: str) -> None:
    """One attempt per schedule. A physical desktop holds one, so overlapping occurrences would
    queue behind each other and arrive as a burst rather than a schedule."""
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    assert _admit(db, schedule_id, journey).admitted

    later = to_rfc3339_utc(datetime(2026, 9, 10, 12, 5, tzinfo=UTC))
    outcome = _admit(db, schedule_id, journey, scheduled_for=later, now=later)
    assert not outcome.admitted
    assert "still running" in (outcome.reason or "")


def test_an_expired_schedule_admits_nothing(db: str) -> None:
    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    after_expiry = "2026-09-21T12:00:00.000000Z"
    outcome = _admit(db, schedule_id, journey, scheduled_for=after_expiry, now=after_expiry)
    assert not outcome.admitted
    assert outcome.reason == "the schedule has expired"


def test_an_occurrence_row_is_always_admitted_or_explained(db: str) -> None:
    """Enforced by the database. A row that is neither is an occurrence nobody can account for."""
    import psycopg

    journey = _journey(db)
    schedule_id = _schedule(db, journey)
    with workspace_connection(db, WS) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO schedule_occurrence
                (id, workspace_id, schedule_id, scheduled_for, admitted)
            VALUES (%s, %s, %s, %s, FALSE)
            """,
            (str(uuid.uuid4()), WS, schedule_id, "2026-09-11T02:00:00.000000Z"),
        )


def test_schedules_are_workspace_isolated(db: str) -> None:
    journey = _journey(db)
    _schedule(db, journey)
    with workspace_connection(db, WS_OTHER) as conn:
        assert conn.execute("SELECT 1 FROM schedule").fetchall() == []
        assert conn.execute("SELECT 1 FROM schedule_occurrence").fetchall() == []
