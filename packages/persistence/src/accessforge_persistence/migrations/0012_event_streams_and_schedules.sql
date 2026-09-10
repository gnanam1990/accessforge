-- Module 19: workspace event streams with a durable cursor, and bounded schedules.
--
-- The cursor is `outbox_message.id`, a BIGSERIAL that module 04 already assigns in commit order.
-- Not a timestamp: two events committed in the same microsecond are indistinguishable by time, and
-- a resume that skipped one because its timestamp matched the cursor would silently drop an event
-- for the rest of that connection. An integer that never repeats is the only cursor a resume can be
-- correct against.

CREATE TABLE IF NOT EXISTS event_stream_position (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    consumer_id   TEXT NOT NULL,
    -- The highest event id this consumer has finished processing. Advanced only after the work is
    -- durable, so a publisher restart re-delivers rather than skips: at-least-once is a property
    -- consumers are built for, and losing a committed event is not recoverable.
    processed_through BIGINT NOT NULL DEFAULT 0 CHECK (processed_through >= 0),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, consumer_id)
);

COMMENT ON COLUMN event_stream_position.processed_through IS
    'Advanced after the work is durable, never before. A publisher restart then re-delivers, which '
    'a consumer is built to tolerate; the alternative loses a committed event, which nothing can.';

-- How far back a reconnecting client may resume. Older than this and the answer is an explicit
-- reset, never a partial stream: a client that resumed from a truncated window would believe it had
-- seen everything since its cursor.
CREATE TABLE IF NOT EXISTS event_retention (
    workspace_id  UUID PRIMARY KEY REFERENCES workspace (id) ON DELETE CASCADE,
    retained_from_event_id BIGINT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schedule (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    name          TEXT NOT NULL,

    -- The standing authorization this schedule draws on, pinned to the revision that was approved.
    -- A grant revised after the schedule was created is a different decision, and a schedule that
    -- followed the reference would quietly run under terms nobody approved.
    execution_grant_id UUID NOT NULL,
    grant_revision BIGINT NOT NULL CHECK (grant_revision >= 1),

    journey_version_id UUID NOT NULL,
    source_ref    TEXT NOT NULL,

    -- Cron-like, with an explicit zone. A schedule without one runs at a different wall-clock hour
    -- twice a year, and "it fired an hour late in March" is a bug nobody can reproduce in July.
    cron_expression TEXT NOT NULL,
    timezone      TEXT NOT NULL,

    -- Paused schedules stay; deleting one loses the record that it ever existed and who stopped it.
    paused_at     TIMESTAMPTZ,
    paused_by     UUID,
    expires_at    TIMESTAMPTZ NOT NULL,

    revision      BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by    UUID NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pause_records_who CHECK ((paused_at IS NULL) = (paused_by IS NULL)),
    FOREIGN KEY (journey_version_id, workspace_id)
        REFERENCES journey_version (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

COMMENT ON COLUMN schedule.grant_revision IS
    'The grant revision approved when this schedule was created. Rechecked at every occurrence: a '
    'grant revised since is a different decision, and following the reference would run under terms '
    'nobody approved.';

-- One row per occurrence, inserted transactionally. The unique key is what makes a missed window,
-- a duplicated timer and two workers racing all resolve to exactly one run.
CREATE TABLE IF NOT EXISTS schedule_occurrence (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    schedule_id   UUID NOT NULL,
    -- The scheduled instant, not the instant work started. Keying on the latter would let two
    -- workers a millisecond apart both believe they owned the occurrence.
    scheduled_for TIMESTAMPTZ NOT NULL,
    run_id        UUID,
    admitted      BOOLEAN NOT NULL DEFAULT FALSE,
    skipped_reason TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT occurrence_is_admitted_or_skipped CHECK (
        (admitted AND run_id IS NOT NULL AND skipped_reason IS NULL)
        OR (NOT admitted AND run_id IS NULL AND skipped_reason IS NOT NULL)
    ),
    FOREIGN KEY (schedule_id, workspace_id) REFERENCES schedule (id, workspace_id)
        ON DELETE CASCADE,
    UNIQUE (schedule_id, scheduled_for)
);

COMMENT ON CONSTRAINT occurrence_is_admitted_or_skipped ON schedule_occurrence IS
    'An occurrence either produced a run or says why it did not. A row that is neither is an '
    'occurrence nobody can account for, and a skipped occurrence with no reason is worse than none.';

CREATE INDEX IF NOT EXISTS schedule_occurrence_by_schedule
    ON schedule_occurrence (schedule_id, scheduled_for DESC);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'event_stream_position', 'event_retention', 'schedule', 'schedule_occurrence'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
