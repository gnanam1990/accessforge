-- Module 17: the identities an outcome binds, and the exports that carry them.
--
-- `run_identity` exists because INV-03 requires an outcome to bind exact versions, and until now
-- those versions lived scattered across the sealed manifest, the journey version, the runner profile
-- and the fixture instance. An exporter reconstructing them by joining five tables would be
-- recomputing at export time what should have been recorded at evaluation time -- and would quietly
-- produce a *current* answer for a run that finished months ago.
--
-- So the identities are written once, when the outcome is admitted, and read verbatim afterwards.

CREATE TABLE IF NOT EXISTS run_identity (
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN (
        'SOURCE', 'BUILD', 'ENVIRONMENT', 'RUNNER_PROFILE', 'EVALUATOR', 'MODEL',
        'JOURNEY_VERSION', 'ASSERTION_SET', 'FIXTURE_INSTANCE'
    )),
    value         TEXT NOT NULL CHECK (length(trim(value)) > 0),
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, kind),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE
);

COMMENT ON TABLE run_identity IS
    'What an outcome binds, recorded when the outcome is admitted rather than reconstructed at read '
    'time. Reconstruction would produce a current answer for a run that finished months ago.';

-- Identities are immutable. A run bound to a different build after the fact is a different run, and
-- an outcome that could be re-pointed is not bound to anything.
CREATE OR REPLACE FUNCTION refuse_run_identity_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'run identity %/% is immutable; an outcome bound to different versions is a different run',
        OLD.run_id, OLD.kind
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS run_identity_is_immutable ON run_identity;
CREATE TRIGGER run_identity_is_immutable
    BEFORE UPDATE OR DELETE ON run_identity
    FOR EACH ROW EXECUTE FUNCTION refuse_run_identity_mutation();

-- One row per export produced. Downloads are audited; the bundle's own contents are not logged, and
-- neither is any signed access URL -- a URL in a log is a bearer credential in a log.
CREATE TABLE IF NOT EXISTS evidence_export (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    attempt_id    UUID NOT NULL,
    requested_by  UUID NOT NULL,

    bundle_digest TEXT NOT NULL CHECK (bundle_digest ~ '^[0-9a-f]{64}$'),
    trust_level   TEXT NOT NULL
        CHECK (trust_level IN ('FULLY_VERIFIABLE', 'LIMITED_DISCLOSURE', 'INCOMPLETE')),
    signing_key_id TEXT NOT NULL,

    -- What the server's retention state was when this bundle was made. Recorded so that a bundle
    -- exported before a deletion cannot later be read as a statement about what the server still
    -- holds: an old export describes a moment, and without this column it would look current.
    retention_snapshot JSONB NOT NULL,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

COMMENT ON COLUMN evidence_export.retention_snapshot IS
    'The retention state of each artifact at the moment of export. An old export never claims '
    'current server retention: it describes what was true when it was made (INV-15).';

CREATE TABLE IF NOT EXISTS evidence_export_download (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    export_id     UUID NOT NULL,
    downloaded_by UUID NOT NULL,
    downloaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (export_id, workspace_id) REFERENCES evidence_export (id, workspace_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE evidence_export_download IS
    'Who downloaded which export and when. Deliberately no URL column and no bundle content: a '
    'signed access URL in an audit log is a bearer credential in an audit log.';

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'run_identity', 'evidence_export', 'evidence_export_download'
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
