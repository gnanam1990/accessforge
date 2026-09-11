-- FR-020, correction: the bytes are deleted *after* the database has committed saying so.
--
-- The first version called the object store inside the caller's transaction. If an earlier object
-- deleted and a later one raised, the transaction rolled back and the store could not: bytes gone,
-- artifact still RETAINED, no deletion record -- and `missing_required_artifacts` would report a
-- **complete** evidence set for a run whose evidence no longer exists. That is precisely the failure
-- this whole feature was built to prevent, reintroduced by the order of two calls.
--
-- So deletion is two phases with a commit between them, and the queue below is what survives it:
--
--   1. Mark the artifacts DELETED, record the deletion, and enqueue every object key. Commit.
--   2. Delete the objects, marking each purged as it goes.
--
-- The inconsistency window now points the safe way. After phase 1 the database says the evidence is
-- deleted while some bytes may still exist; completeness is already invalidated, nobody is told the
-- bytes are gone until they are, and a retry finishes the job. The failure the old order produced --
-- bytes gone, database saying retained -- cannot happen, because the database commits first.
--
-- A queue rather than a flag on the artifact: one artifact has two objects (the original and any
-- redacted view), and a key that fails to delete needs its own error and its own retry count.

CREATE TABLE IF NOT EXISTS evidence_object_purge (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- Which deletion asked for this. RESTRICT, not CASCADE: a pending purge is unfinished work, and
    -- removing the record of why it exists would leave a key nobody can explain.
    deletion_id   UUID NOT NULL REFERENCES evidence_deletion (id) ON DELETE RESTRICT,

    -- The artifact this key belongs to, for the operator who has to explain a stuck row.
    artifact_id   UUID NOT NULL,
    object_key    TEXT NOT NULL,

    -- NULL until the object is actually gone from the store. This column is the whole point: it is
    -- the difference between "the database says deleted" and "the bytes are deleted", and a report
    -- that conflated the two would be the lie this table exists to prevent.
    purged_at     TIMESTAMPTZ,

    attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error    TEXT,
    enqueued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One row per key per deletion. A repeated enqueue is a retry of the same work, not new work.
    UNIQUE (deletion_id, object_key)
);

-- Partial, on exactly the rows a retry looks for. The done rows are the overwhelming majority and
-- an operator sweeping for unfinished work should not scan them.
CREATE INDEX IF NOT EXISTS evidence_object_purge_pending
    ON evidence_object_purge (enqueued_at) WHERE purged_at IS NULL;

ALTER TABLE evidence_object_purge ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_object_purge FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_isolation ON evidence_object_purge;
CREATE POLICY workspace_isolation ON evidence_object_purge
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

COMMENT ON TABLE evidence_object_purge IS
    'Object keys a committed deletion has promised to remove. Rows with purged_at IS NULL are bytes '
    'the database already reports as deleted and the store still holds -- unfinished work, not an '
    'inconsistency to hide.';

-- The run foreign key on evidence_deletion said ON DELETE CASCADE while the table comment said the
-- record must survive its subject. The comment was right and the constraint was not: a run deletion
-- -- not possible today, and the kind of thing a later migration adds -- would have removed the
-- audit record along with it. RESTRICT makes the documented contract the enforced one.
ALTER TABLE evidence_deletion
    DROP CONSTRAINT IF EXISTS evidence_deletion_run_id_workspace_id_fkey;

ALTER TABLE evidence_deletion
    ADD CONSTRAINT evidence_deletion_run_id_workspace_id_fkey
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE RESTRICT;
