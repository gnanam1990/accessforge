-- Module 06: journey versions and per-run fixture instances.
--
-- Journey versions are immutable. Editing a journey creates a new version with a new digest, because
-- success criteria that could change mid-run would make every outcome provisional (INV-05, INV-16).
--
-- Fixture instances are fresh per run. A reused instance carries a prior run's receipt, and a
-- completion assertion satisfiable by an earlier run's row is not testing anything.

CREATE TABLE IF NOT EXISTS journey_version (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id    UUID NOT NULL,
    name          TEXT NOT NULL,
    platform      TEXT NOT NULL,
    journey_digest         TEXT NOT NULL CHECK (journey_digest ~ '^[0-9a-f]{64}$'),
    assertion_set_digest   TEXT NOT NULL CHECK (assertion_set_digest ~ '^[0-9a-f]{64}$'),
    fixture_digest         TEXT NOT NULL CHECK (fixture_digest ~ '^[0-9a-f]{64}$'),
    navigator_policy_digest TEXT NOT NULL CHECK (navigator_policy_digest ~ '^[0-9a-f]{64}$'),
    -- Stored for review and replay. The navigator policy is what the navigator may see; it contains
    -- no oracle material by construction, which the compile step enforces.
    navigator_policy JSONB NOT NULL,
    reviewer_summary JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    supersedes    UUID REFERENCES journey_version (id),
    FOREIGN KEY (project_id, workspace_id) REFERENCES project (id, workspace_id) ON DELETE CASCADE
);

COMMENT ON COLUMN journey_version.supersedes IS
    'Append-only lineage. A proposed task change is a new version requiring review, never a repair '
    'of a failed run.';

CREATE OR REPLACE FUNCTION refuse_journey_version_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'journey version % is immutable; editing creates a new version', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS journey_version_is_immutable ON journey_version;
CREATE TRIGGER journey_version_is_immutable
    BEFORE UPDATE ON journey_version
    FOR EACH ROW EXECUTE FUNCTION refuse_journey_version_mutation();

-- Named run_fixture_instance, not fixture_instance. The reference application under test has its
-- own fixture_instance table, and in local development both live in the same database. A collision
-- there would have been silent: CREATE TABLE IF NOT EXISTS simply does nothing, and the product
-- would have been reading the application's rows. The product must not share names with the
-- application it tests.
CREATE TABLE IF NOT EXISTS run_fixture_instance (
    id              UUID PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id          UUID NOT NULL,
    template_id     TEXT NOT NULL,
    template_digest TEXT NOT NULL CHECK (template_digest ~ '^[0-9a-f]{64}$'),
    nonce           TEXT NOT NULL,
    navigator_values JSONB NOT NULL,
    -- The answer key. Read only through observer_config(), never through the navigator path.
    observer_config  JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One instance per run: freshness enforced by the database, not by a convention.
    UNIQUE (run_id),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE
);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['journey_version', 'run_fixture_instance'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
