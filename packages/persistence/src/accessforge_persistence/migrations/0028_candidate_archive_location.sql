-- Preserve original capture location; legacy records stay unbound, never guessed at.
ALTER TABLE candidate_archive ADD COLUMN store_endpoint TEXT;
ALTER TABLE candidate_archive ADD COLUMN store_bucket TEXT;
ALTER TABLE candidate_archive ADD CONSTRAINT candidate_store_location_pair
    CHECK ((store_endpoint IS NULL) = (store_bucket IS NULL));
CREATE TABLE candidate_archive_restore_location (
    build_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    restore_id TEXT NOT NULL,
    store_endpoint TEXT NOT NULL,
    store_bucket TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (build_id, revision),
    UNIQUE (build_id, restore_id),
    FOREIGN KEY (build_id,workspace_id) REFERENCES candidate_archive(build_id,workspace_id)
        ON DELETE CASCADE
);
CREATE FUNCTION preserve_candidate_restore_location() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'candidate restore location is immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER candidate_restore_location_is_immutable
    BEFORE UPDATE ON candidate_archive_restore_location
    FOR EACH ROW EXECUTE FUNCTION preserve_candidate_restore_location();
ALTER TABLE candidate_archive_restore_location ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_archive_restore_location FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_archive_restore_location
    USING (workspace_id = current_workspace_id()) WITH CHECK (workspace_id = current_workspace_id());
