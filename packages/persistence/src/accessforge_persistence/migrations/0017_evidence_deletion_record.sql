-- FR-020: a deletion leaves a record of itself, and the record is not optional.
--
-- The table exists because of the sentence in FR-020 that says deletion must reach primary objects,
-- derived views, exports, caches and supported backups -- and then says local downloaded copies
-- cannot be remotely recalled. A deletion that could not be demonstrated afterwards would leave
-- nobody able to answer the two questions that follow one: what was removed, and what was not.
--
-- **This is the one thing a deletion does not delete.** An erasure nobody can prove is worse than no
-- erasure at all: the data may be gone and the organisation still cannot say so. So these rows
-- survive their own subject, and `evidence_classes` names what was covered rather than implying it
-- from what is now missing -- "absent" and "deleted" are different facts, and a reader who cannot
-- tell them apart will assume whichever suits them.
--
-- Deliberately no `ON DELETE CASCADE` from `run`: a run row is never deleted in this system, and if
-- that ever changed, the deletion record is the last thing that should go with it.

CREATE TABLE IF NOT EXISTS evidence_deletion (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,

    -- NULL means the whole run rather than one attempt. Recorded as NULL instead of repeating every
    -- attempt id, because the scope somebody *chose* is the fact worth keeping; which attempts
    -- existed at the time is derivable and changes meaning if a retry is added later.
    attempt_id    UUID,

    -- What was covered. A text array rather than a join table: the set is small, closed, and read
    -- as a whole every time. It is also a snapshot -- a class renamed later must not silently
    -- rewrite what a past deletion says it removed.
    evidence_classes TEXT[] NOT NULL CHECK (cardinality(evidence_classes) > 0),

    -- Required, and checked here as well as in the application. A deletion with an empty reason is
    -- the one an auditor asks about first, and the database is the only place the constraint cannot
    -- be bypassed by a new caller.
    reason        TEXT NOT NULL CHECK (length(btrim(reason)) > 0),

    -- ON DELETE SET NULL rather than RESTRICT: a person may leave the organisation, and their
    -- departure must not make a deletion record undeletable or, worse, cascade it away.
    requested_by  UUID REFERENCES app_user (id) ON DELETE SET NULL,

    -- Counts, for a report somebody can check against the runbook. Not a substitute for the
    -- tombstones: these say how many, and `evidence_artifact.retention = 'DELETED'` says which.
    artifact_bytes_deleted  INTEGER NOT NULL CHECK (artifact_bytes_deleted >= 0),
    event_payloads_cleared  INTEGER NOT NULL CHECK (event_payloads_cleared >= 0),

    -- Recorded at the time, not derived on read. Whether a class breaks a completeness claim is a
    -- property of the policy as it stood when the deletion happened, and a later policy edit must
    -- not change what a past deletion cost.
    completeness_invalidated BOOLEAN NOT NULL,

    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS evidence_deletion_by_run
    ON evidence_deletion (run_id, requested_at);

ALTER TABLE evidence_deletion ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_deletion FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_isolation ON evidence_deletion;
CREATE POLICY workspace_isolation ON evidence_deletion
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

COMMENT ON TABLE evidence_deletion IS
    'The record a deletion leaves of itself. Never deleted by a deletion: an erasure nobody can '
    'prove is worse than none, because the data may be gone and the organisation still cannot say so.';
