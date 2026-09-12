ALTER TABLE sealed_manifest ADD CONSTRAINT sealed_manifest_id_workspace_key UNIQUE(id,workspace_id);
ALTER TABLE run_fixture_instance ADD COLUMN captured_contract_digest TEXT
 CHECK(captured_contract_digest ~ '^[a-f0-9]{64}$');
CREATE TABLE candidate_run_binding (
 run_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 regression_attempt_id UUID NOT NULL UNIQUE,
 build_id UUID NOT NULL,
 verification_id UUID NOT NULL UNIQUE,
 baseline_run_id UUID NOT NULL,
 sealed_manifest_id UUID NOT NULL,
 fixture_nonce TEXT NOT NULL,
 fixture_template_digest TEXT NOT NULL CHECK (fixture_template_digest ~ '^[a-f0-9]{64}$'),
 fixture_contract_digest TEXT NOT NULL CHECK (fixture_contract_digest ~ '^[a-f0-9]{64}$'),
 endpoint_binding_digest TEXT NOT NULL CHECK (endpoint_binding_digest ~ '^[a-f0-9]{64}$'),
 permitted_differences JSONB NOT NULL CHECK (jsonb_typeof(permitted_differences)='array'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,workspace_id), UNIQUE(workspace_id,fixture_nonce),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(baseline_run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(regression_attempt_id,workspace_id) REFERENCES candidate_regression_attempt(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(build_id,workspace_id) REFERENCES candidate_build_attempt(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(verification_id,workspace_id) REFERENCES patch_verification(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(sealed_manifest_id,workspace_id) REFERENCES sealed_manifest(id,workspace_id) ON DELETE CASCADE
);
CREATE TABLE candidate_reader_lease (
 run_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 lease_id UUID NOT NULL UNIQUE,
 lease_epoch BIGINT NOT NULL CHECK (lease_epoch>=1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,workspace_id) REFERENCES candidate_run_binding(run_id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(lease_id,workspace_id) REFERENCES desktop_lease(id,workspace_id) ON DELETE CASCADE
);
CREATE FUNCTION refuse_candidate_binding_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'candidate binding or captured fixture is immutable'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER candidate_run_binding_immutable BEFORE UPDATE ON candidate_run_binding
 FOR EACH ROW EXECUTE FUNCTION refuse_candidate_binding_mutation();
CREATE TRIGGER candidate_reader_lease_immutable BEFORE UPDATE ON candidate_reader_lease
 FOR EACH ROW EXECUTE FUNCTION refuse_candidate_binding_mutation();
CREATE TRIGGER captured_fixture_immutable BEFORE UPDATE ON run_fixture_instance
 FOR EACH ROW EXECUTE FUNCTION refuse_candidate_binding_mutation();
CREATE FUNCTION guard_verification_candidate_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM candidate_run_binding b WHERE b.verification_id=NEW.id
           AND NEW.candidate_run_id IS DISTINCT FROM b.run_id) THEN
   RAISE EXCEPTION 'verification cannot select another bound candidate run'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER verification_candidate_binding BEFORE UPDATE ON patch_verification
 FOR EACH ROW EXECUTE FUNCTION guard_verification_candidate_binding();
ALTER TABLE candidate_run_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_run_binding FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_run_binding
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
ALTER TABLE candidate_reader_lease ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_reader_lease FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_reader_lease
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
COMMENT ON TABLE candidate_run_binding IS
 'Exact live endpoint, build, verification, fresh fixture and canonical seal. Preparation is not '
 'RUN_EFFECTS authorization or reader evidence; original regression/endpoint authority must stay live.';
