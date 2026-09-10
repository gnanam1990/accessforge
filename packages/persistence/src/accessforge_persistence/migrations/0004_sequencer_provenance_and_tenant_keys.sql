-- Module 04, second migration: close two defects found in independent review.
--
-- 1. Replay reported the wrong canonical event when two source records shared a payload.
--
--    The replay path searched `canonical_event` by `payload_digest` and took the first match. Two
--    identical reader observations — a screen reader saying "Loading" twice — are two distinct
--    source records with the same content, so redelivering the second reported the first one's
--    position and event id. Reproduced.
--
--    Content is how a replay is told apart from a conflict. It is not an identity. The canonical
--    position is now recorded on the source record itself, so a replay answers from provenance.
--
-- 2. A session could sequence onto another workspace's attempt.
--
--    `admit_record` trusted the caller's workspace_id and never checked it against the workspace
--    that owns the run. Referential-integrity checks bypass row-level security, so a session scoped
--    to A could insert a row labelled A chained onto B's (run, attempt) sequence space.
--
--    The consequence was a cross-tenant denial of service, not just pollution: B could not see the
--    injected row, but it occupied canonical position 1, so B's own first record failed with a bare
--    UniqueViolation on a chain that looked empty from inside B. Composite foreign keys now make the
--    mismatch impossible at the database level, whatever the application does.

-- ---------------------------------------------------------------------------------------------
-- 1. Provenance-keyed replay
-- ---------------------------------------------------------------------------------------------
ALTER TABLE producer_source_record
    ADD COLUMN IF NOT EXISTS canonical_sequence BIGINT,
    ADD COLUMN IF NOT EXISTS event_id UUID;

COMMENT ON COLUMN producer_source_record.canonical_sequence IS
    'The canonical position this source record was admitted at. Recorded so a replay answers from '
    'provenance rather than by searching for a matching payload, which returned a twin.';

-- Backfill is unambiguous only where a digest maps to exactly one event. Anything ambiguous is left
-- NULL rather than guessed at, and the code treats NULL as "cannot confirm".
UPDATE producer_source_record p
SET canonical_sequence = e.sequence, event_id = e.event_id
FROM canonical_event e
WHERE e.run_id = p.run_id
  AND e.attempt_id = p.attempt_id
  AND e.payload_digest = p.source_record_digest
  AND p.canonical_sequence IS NULL
  AND (
      SELECT count(*) FROM canonical_event c
      WHERE c.run_id = p.run_id AND c.attempt_id = p.attempt_id
        AND c.payload_digest = p.source_record_digest
  ) = 1;

-- ---------------------------------------------------------------------------------------------
-- 2. Composite tenant keys
-- ---------------------------------------------------------------------------------------------
-- Targets for composite references. The primary keys already guarantee uniqueness of the id alone;
-- these let a child row require that the parent belongs to the same workspace.
ALTER TABLE run
    ADD CONSTRAINT run_id_workspace_key UNIQUE (id, workspace_id);
ALTER TABLE run_attempt
    ADD CONSTRAINT run_attempt_id_workspace_key UNIQUE (id, workspace_id);

-- Replace the id-only references with composite ones. A row can no longer name a run or attempt
-- belonging to a different workspace, regardless of what the application believes.
ALTER TABLE canonical_event
    DROP CONSTRAINT IF EXISTS canonical_event_run_id_fkey,
    DROP CONSTRAINT IF EXISTS canonical_event_attempt_id_fkey,
    ADD CONSTRAINT canonical_event_run_in_workspace
        FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    ADD CONSTRAINT canonical_event_attempt_in_workspace
        FOREIGN KEY (attempt_id, workspace_id)
        REFERENCES run_attempt (id, workspace_id) ON DELETE CASCADE;

ALTER TABLE producer_stream
    DROP CONSTRAINT IF EXISTS producer_stream_run_id_fkey,
    DROP CONSTRAINT IF EXISTS producer_stream_attempt_id_fkey,
    ADD CONSTRAINT producer_stream_run_in_workspace
        FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    ADD CONSTRAINT producer_stream_attempt_in_workspace
        FOREIGN KEY (attempt_id, workspace_id)
        REFERENCES run_attempt (id, workspace_id) ON DELETE CASCADE;

ALTER TABLE producer_source_record
    DROP CONSTRAINT IF EXISTS producer_source_record_run_id_fkey,
    DROP CONSTRAINT IF EXISTS producer_source_record_attempt_id_fkey,
    ADD CONSTRAINT producer_source_record_run_in_workspace
        FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    ADD CONSTRAINT producer_source_record_attempt_in_workspace
        FOREIGN KEY (attempt_id, workspace_id)
        REFERENCES run_attempt (id, workspace_id) ON DELETE CASCADE;

-- The same reasoning applies to child authorizations and attempts themselves.
ALTER TABLE child_authorization
    DROP CONSTRAINT IF EXISTS child_authorization_run_id_fkey,
    ADD CONSTRAINT child_authorization_run_in_workspace
        FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE;

ALTER TABLE run_attempt
    DROP CONSTRAINT IF EXISTS run_attempt_run_id_fkey,
    ADD CONSTRAINT run_attempt_run_in_workspace
        FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE;
