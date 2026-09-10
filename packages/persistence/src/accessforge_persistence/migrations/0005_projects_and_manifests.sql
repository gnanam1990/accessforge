-- Module 05: authorized projects, environments and immutable sealed identities.
--
-- The governing rule: a mutable reference is never an identity. A branch name, an image tag and a
-- deployment URL can all change while keeping the same text, so none of them may stand in for the
-- bytes a run actually used. Every digest column below is a fixed-width hex check for that reason —
-- a column that accepted arbitrary text would eventually hold "latest".
--
-- Records are append-only. Revocation makes future use unavailable; it never rewrites the identity
-- of evidence already produced (INV-11).

CREATE TABLE IF NOT EXISTS project (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    -- Where source may be read from. Authorization is recorded explicitly: public reachability of a
    -- repository is not consent to automate against it.
    repository_url TEXT,
    repository_authorized_by UUID REFERENCES app_user (id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision      BIGINT      NOT NULL DEFAULT 0,
    revoked_at    TIMESTAMPTZ,
    UNIQUE (workspace_id, name)
);

-- Declared here, not later: the tables below require a same-workspace parent, and a composite
-- foreign key needs a matching unique constraint to already exist.
ALTER TABLE project ADD CONSTRAINT project_id_workspace_key UNIQUE (id, workspace_id);

CREATE TABLE IF NOT EXISTS environment_manifest (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id    UUID NOT NULL,
    name          TEXT NOT NULL,
    -- Normalized origins, scheme://host[:port]. Path is deliberately excluded: an allowlist entry
    -- narrowed to a path could be widened by navigating elsewhere on the same host.
    allowed_origins TEXT[] NOT NULL CHECK (cardinality(allowed_origins) > 0),
    fixture_reset_strategy TEXT NOT NULL,
    -- Stable references only. A secret value in a manifest would travel into every export and
    -- evidence bundle that cites it.
    observer_credential_ref TEXT NOT NULL,
    reset_credential_ref    TEXT NOT NULL,
    permitted_effects TEXT[] NOT NULL DEFAULT '{}',
    authorized_by  UUID NOT NULL REFERENCES app_user (id),
    config_digest  TEXT NOT NULL CHECK (config_digest ~ '^[0-9a-f]{64}$'),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision       BIGINT      NOT NULL DEFAULT 0,
    revoked_at     TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ NOT NULL,
    superseded_by  UUID REFERENCES environment_manifest (id),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id) ON DELETE CASCADE
);

ALTER TABLE environment_manifest
    ADD CONSTRAINT environment_manifest_id_workspace_key UNIQUE (id, workspace_id);

COMMENT ON COLUMN environment_manifest.superseded_by IS
    'Append-only history. A changed environment creates a new manifest and points the old one at it, '
    'so evidence citing the old identity keeps meaning what it meant.';

COMMENT ON COLUMN environment_manifest.observer_credential_ref IS
    'The independent observer reads application state; the navigator must never hold this. Separating '
    'the two references is what stops the observer oracle becoming navigator input.';

CREATE TABLE IF NOT EXISTS source_snapshot (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id    UUID NOT NULL,
    -- Resolved immediately from whatever ref was requested, so a later force push cannot change
    -- what this row refers to.
    commit_sha    TEXT NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    tree_digest   TEXT NOT NULL CHECK (tree_digest ~ '^[0-9a-f]{64}$'),
    -- Recorded, never inferred. A dirty tree shares the commit's name but not its bytes.
    dirty         BOOLEAN NOT NULL,
    dirty_path_count INTEGER NOT NULL DEFAULT 0 CHECK (dirty_path_count >= 0),
    requested_revision TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id) ON DELETE CASCADE,
    CONSTRAINT source_snapshot_dirty_pair CHECK ((dirty_path_count > 0) = dirty)
);

ALTER TABLE source_snapshot ADD CONSTRAINT source_snapshot_id_workspace_key UNIQUE (id, workspace_id);

COMMENT ON CONSTRAINT source_snapshot_dirty_pair ON source_snapshot IS
    'A snapshot cannot claim to be clean while listing modified paths, or claim modifications '
    'without naming how many.';

CREATE TABLE IF NOT EXISTS build_artifact (
    id             UUID PRIMARY KEY,
    workspace_id   UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id     UUID NOT NULL,
    source_snapshot_id UUID NOT NULL,
    -- Separate from the source digest on purpose: the same source can produce different artifacts,
    -- and conflating them would hide a substituted build behind an unchanged commit.
    artifact_digest TEXT NOT NULL CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    -- Whether the artifact identity was actually observed, or is merely asserted by a deployment
    -- that cannot prove what it is serving.
    identity_observable BOOLEAN NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (source_snapshot_id, workspace_id)
        REFERENCES source_snapshot (id, workspace_id) ON DELETE CASCADE
);

ALTER TABLE build_artifact ADD CONSTRAINT build_artifact_id_workspace_key UNIQUE (id, workspace_id);

COMMENT ON COLUMN build_artifact.identity_observable IS
    'False for a deployment that cannot prove which artifact it serves. Such a run may still '
    'execute, but it cannot make a fully verified provenance claim, and this column is how that '
    'limitation stays visible instead of being filled in with an invented digest.';

CREATE TABLE IF NOT EXISTS sealed_manifest (
    id               UUID PRIMARY KEY,
    workspace_id     UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id       UUID NOT NULL,
    run_id           UUID,
    -- Every sealed input, captured separately. CONTRACTS section 4 lists these; conflating any two
    -- would let one change hide behind another.
    source_snapshot_id   UUID NOT NULL,
    build_artifact_id    UUID NOT NULL,
    environment_manifest_id UUID NOT NULL,
    environment_config_digest TEXT NOT NULL CHECK (environment_config_digest ~ '^[0-9a-f]{64}$'),
    journey_digest       TEXT NOT NULL CHECK (journey_digest ~ '^[0-9a-f]{64}$'),
    assertion_set_digest TEXT NOT NULL CHECK (assertion_set_digest ~ '^[0-9a-f]{64}$'),
    fixture_digest       TEXT NOT NULL CHECK (fixture_digest ~ '^[0-9a-f]{64}$'),
    runner_profile_digest TEXT NOT NULL CHECK (runner_profile_digest ~ '^[0-9a-f]{64}$'),
    navigator_policy_digest TEXT NOT NULL CHECK (navigator_policy_digest ~ '^[0-9a-f]{64}$'),
    evaluator_version    TEXT NOT NULL,
    model_config_digest  TEXT NOT NULL CHECK (model_config_digest ~ '^[0-9a-f]{64}$'),
    -- The digest of the whole sealed manifest. Changing any input changes this.
    manifest_digest      TEXT NOT NULL UNIQUE CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    authorization_id     UUID,
    sealed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (source_snapshot_id, workspace_id)
        REFERENCES source_snapshot (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (build_artifact_id, workspace_id)
        REFERENCES build_artifact (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_manifest_id, workspace_id)
        REFERENCES environment_manifest (id, workspace_id) ON DELETE CASCADE
);

COMMENT ON TABLE sealed_manifest IS
    'Immutable once written. A changed input requires a new seal, never an amendment — which is why '
    'there is no UPDATE path and a trigger refuses one.';

CREATE OR REPLACE FUNCTION refuse_sealed_manifest_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'sealed manifest % is immutable; a changed input requires a new seal', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS sealed_manifest_is_immutable ON sealed_manifest;
CREATE TRIGGER sealed_manifest_is_immutable
    BEFORE UPDATE ON sealed_manifest
    FOR EACH ROW EXECUTE FUNCTION refuse_sealed_manifest_mutation();

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'project', 'environment_manifest', 'source_snapshot', 'build_artifact', 'sealed_manifest'
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
