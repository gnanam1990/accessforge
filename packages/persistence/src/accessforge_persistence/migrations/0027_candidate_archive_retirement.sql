-- NULL identifies legacy unconditional uploaders; never infer retirement safety for them.
ALTER TABLE candidate_archive ADD COLUMN storage_protocol TEXT
    CHECK (storage_protocol IS NULL OR storage_protocol = 'CREATE_ONLY_V1');
ALTER TABLE candidate_archive ADD CONSTRAINT candidate_archive_id_workspace_key
    UNIQUE (build_id, workspace_id);

CREATE TABLE candidate_archive_retirement (
    build_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    policy_revision BIGINT NOT NULL CHECK (policy_revision >= 0),
    retain_days BIGINT NOT NULL CHECK (retain_days >= 0),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (build_id, workspace_id) REFERENCES candidate_archive(build_id, workspace_id)
        ON DELETE CASCADE
);
CREATE FUNCTION preserve_candidate_retirement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'completed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'completed_at')
       OR OLD.completed_at IS NOT NULL THEN
        RAISE EXCEPTION 'candidate retirement intent is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_retirement_is_immutable BEFORE UPDATE ON candidate_archive_retirement
    FOR EACH ROW EXECUTE FUNCTION preserve_candidate_retirement();
ALTER TABLE candidate_archive_retirement ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_archive_retirement FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_archive_retirement
    USING (workspace_id = current_workspace_id()) WITH CHECK (workspace_id = current_workspace_id());
COMMENT ON TABLE candidate_archive_retirement IS
    'Committed intent makes bytes unavailable before I/O. Completion means payload replaced with '
    'a permanent empty tombstone in an unversioned, unreplicated, non-expiring object namespace; '
    'not erasure of prior backups. Late create-only uploads cannot replace a tombstone.';
