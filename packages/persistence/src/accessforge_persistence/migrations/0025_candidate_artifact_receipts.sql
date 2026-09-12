ALTER TABLE candidate_build_attempt ADD CONSTRAINT candidate_build_id_workspace_key
    UNIQUE (id, workspace_id);

CREATE TABLE candidate_process_receipt (
    build_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    container_id TEXT NOT NULL CHECK (container_id ~ '^[a-f0-9]{64}$'),
    image_id TEXT NOT NULL CHECK (image_id ~ '^sha256:[a-f0-9]{64}$'),
    platform TEXT NOT NULL CHECK (platform ~ '^linux/[a-z0-9]+(/[a-z0-9]+)?$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (build_id, workspace_id) REFERENCES candidate_build_attempt (id, workspace_id)
        ON DELETE CASCADE,
    UNIQUE (build_id, workspace_id)
);
CREATE FUNCTION preserve_candidate_process_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'candidate process receipt is immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER candidate_process_is_immutable BEFORE UPDATE ON candidate_process_receipt
    FOR EACH ROW EXECUTE FUNCTION preserve_candidate_process_receipt();

CREATE TABLE candidate_archive (
    build_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    content_digest TEXT NOT NULL CHECK (content_digest ~ '^[a-f0-9]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 41943040),
    object_key TEXT NOT NULL UNIQUE,
    stdout_digest TEXT NOT NULL CHECK (stdout_digest ~ '^[a-f0-9]{64}$'),
    stderr_digest TEXT NOT NULL CHECK (stderr_digest ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('QUARANTINED', 'RETAINED', 'DELETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    retained_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    FOREIGN KEY (build_id, workspace_id) REFERENCES candidate_process_receipt (build_id, workspace_id)
        ON DELETE CASCADE,
    CHECK ((state = 'RETAINED') = (retained_at IS NOT NULL AND deleted_at IS NULL)),
    CHECK ((state = 'DELETED') = (deleted_at IS NOT NULL))
);
CREATE FUNCTION preserve_candidate_archive_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['state','retained_at','deleted_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['state','retained_at','deleted_at']) THEN
        RAISE EXCEPTION 'candidate archive identity is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_archive_is_immutable BEFORE UPDATE ON candidate_archive
    FOR EACH ROW EXECUTE FUNCTION preserve_candidate_archive_identity();

ALTER TABLE candidate_process_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_process_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_process_receipt
    USING (workspace_id = current_workspace_id()) WITH CHECK (workspace_id = current_workspace_id());
ALTER TABLE candidate_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_archive FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_archive
    USING (workspace_id = current_workspace_id()) WITH CHECK (workspace_id = current_workspace_id());
COMMENT ON TABLE candidate_archive IS
    'Executable build bytes are a separate quarantined namespace, never transcript evidence. '
    'Retained only after bounded object read-back and a fresh attempt fence check.';
