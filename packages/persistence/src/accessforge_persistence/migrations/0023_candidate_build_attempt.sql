-- Build execution is not a retryable generic job. Expiry fences an attempt; it never requeues it.
ALTER TABLE approval ADD CONSTRAINT approval_id_workspace_key UNIQUE (id, workspace_id);
ALTER TABLE patch_verification ADD CONSTRAINT patch_verification_id_workspace_key
    UNIQUE (id, workspace_id);

CREATE TABLE candidate_build_attempt (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    patch_id UUID NOT NULL UNIQUE,
    verification_id UUID NOT NULL,
    project_id UUID NOT NULL,
    source_snapshot_id UUID NOT NULL,
    approval_id UUID NOT NULL,
    approved_revision BIGINT NOT NULL CHECK (approved_revision >= 1),
    building_revision BIGINT NOT NULL CHECK (building_revision = approved_revision + 1),
    source_commit TEXT NOT NULL CHECK (source_commit ~ '^[a-f0-9]{40}$'),
    source_tree_digest TEXT NOT NULL CHECK (source_tree_digest ~ '^[a-f0-9]{64}$'),
    base_archive_digest TEXT NOT NULL CHECK (base_archive_digest ~ '^[a-f0-9]{64}$'),
    candidate_archive_digest TEXT NOT NULL CHECK (candidate_archive_digest ~ '^[a-f0-9]{64}$'),
    patch_digest TEXT NOT NULL CHECK (patch_digest ~ '^[a-f0-9]{64}$'),
    policy_digest TEXT NOT NULL CHECK (policy_digest ~ '^[a-f0-9]{64}$'),
    surface_digest TEXT NOT NULL CHECK (surface_digest ~ '^[a-f0-9]{64}$'),
    worker_token UUID NOT NULL,
    epoch BIGINT NOT NULL DEFAULT 1 CHECK (epoch >= 1),
    state TEXT NOT NULL CHECK (state IN ('CLAIMED', 'DISPATCHED', 'BUILT', 'FAILED', 'UNKNOWN')),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    dispatched_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    artifact_digest TEXT CHECK (artifact_digest ~ '^[a-f0-9]{64}$'),
    cleanup_confirmed BOOLEAN NOT NULL DEFAULT false,
    failure_code TEXT,
    FOREIGN KEY (patch_id, workspace_id) REFERENCES patch_proposal (id, workspace_id),
    FOREIGN KEY (verification_id, workspace_id) REFERENCES patch_verification (id, workspace_id),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id),
    FOREIGN KEY (source_snapshot_id, workspace_id) REFERENCES source_snapshot (id, workspace_id),
    FOREIGN KEY (approval_id, workspace_id) REFERENCES approval (id, workspace_id),
    CHECK ((state = 'BUILT') = (artifact_digest IS NOT NULL)),
    CHECK (state <> 'BUILT' OR (cleanup_confirmed AND dispatched_at IS NOT NULL)),
    CHECK ((state IN ('BUILT', 'FAILED', 'UNKNOWN')) = (finished_at IS NOT NULL))
);
CREATE INDEX candidate_build_expiry ON candidate_build_attempt (lease_expires_at)
    WHERE state IN ('CLAIMED', 'DISPATCHED');
ALTER TABLE candidate_build_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_build_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_build_attempt
    USING (workspace_id = current_workspace_id()) WITH CHECK (workspace_id = current_workspace_id());
COMMENT ON TABLE candidate_build_attempt IS
    'One fenced attempt per approved patch. ID determines the task container name before creation. '
    'UNKNOWN requires exact-container reconciliation, never automatic requeue. BUILT is not VERIFIED.';

CREATE FUNCTION preserve_candidate_build_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['state','epoch','dispatched_at','finished_at','artifact_digest',
                             'cleanup_confirmed','failure_code']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['state','epoch','dispatched_at','finished_at','artifact_digest',
                             'cleanup_confirmed','failure_code']) THEN
        RAISE EXCEPTION 'candidate build input and ownership identity is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_build_identity_is_immutable BEFORE UPDATE ON candidate_build_attempt
    FOR EACH ROW EXECUTE FUNCTION preserve_candidate_build_identity();
