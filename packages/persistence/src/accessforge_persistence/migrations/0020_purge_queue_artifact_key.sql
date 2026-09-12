-- FR-020 integrity: a purge row must name a real artifact in its own workspace.
--
-- `evidence_object_purge.artifact_id` carried no foreign key. In practice it is only ever written
-- from a row selected in the same transaction, so nothing has gone wrong -- but "nothing has gone
-- wrong yet" is not an integrity guarantee, and the row it would corrupt is the one an operator
-- reads to explain a stuck purge. A key naming an artifact that does not exist is a key nobody can
-- attribute, in the table whose whole job is saying which bytes are still out there.
--
-- Composite (artifact_id, workspace_id), not a plain reference to the primary key. Foreign key
-- checks run as the table owner and bypass row-level security, so a single-column reference would
-- happily point at another tenant's artifact: the check would pass, and a purge row in workspace A
-- would name evidence in workspace B. Carrying workspace_id into the constraint is how every other
-- cross-table reference in this schema is written, for the same reason.
--
-- RESTRICT rather than CASCADE: artifacts are never deleted here -- deletion leaves a tombstone
-- (INV-15) -- so this can only fire if some future migration tries to remove one, and taking the
-- record of an unfinished purge with it is exactly what must not happen silently.

-- The composite key the reference needs. `id` is already the primary key, so this adds no new
-- uniqueness; it gives the foreign key something to point at.
ALTER TABLE evidence_artifact
    DROP CONSTRAINT IF EXISTS evidence_artifact_id_workspace_key;

ALTER TABLE evidence_artifact
    ADD CONSTRAINT evidence_artifact_id_workspace_key UNIQUE (id, workspace_id);

-- NOT VALID first, then validated as its own statement. ADD CONSTRAINT on its own scans every
-- existing row while holding SHARE ROW EXCLUSIVE on both tables, so writes to evidence_artifact --
-- which every running attempt performs -- wait for the scan. NOT VALID holds that lock only long
-- enough to record the constraint, and VALIDATE CONSTRAINT scans under SHARE UPDATE EXCLUSIVE,
-- which does not block writes. New rows are checked from the moment the constraint exists either
-- way.
ALTER TABLE evidence_object_purge
    DROP CONSTRAINT IF EXISTS evidence_object_purge_artifact_fkey;

ALTER TABLE evidence_object_purge
    ADD CONSTRAINT evidence_object_purge_artifact_fkey
    FOREIGN KEY (artifact_id, workspace_id)
    REFERENCES evidence_artifact (id, workspace_id) ON DELETE RESTRICT
    NOT VALID;

ALTER TABLE evidence_object_purge
    VALIDATE CONSTRAINT evidence_object_purge_artifact_fkey;
