-- Original verifier snapshot, not a mutable latest-summary cache. No historical verdict backfill.
CREATE TABLE run_evaluation (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id),
    run_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    manifest_digest TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    evidence_set_digest TEXT NOT NULL CHECK (evidence_set_digest ~ '^[0-9a-f]{64}$'),
    snapshot_digest TEXT NOT NULL CHECK (snapshot_digest ~ '^[0-9a-f]{64}$'),
    evaluator_version TEXT NOT NULL CHECK (length(evaluator_version)>0),
    outcome TEXT NOT NULL CHECK (outcome IN ('PASS','FAIL','INCONCLUSIVE')),
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id,workspace_id),
    FOREIGN KEY (attempt_id,run_id,workspace_id)
      REFERENCES run_attempt(id,run_id,workspace_id),
    CHECK (snapshot->>'outcome'=outcome),
    CHECK (snapshot ?& ARRAY['outcome','runId','attemptId','evidenceSetDigest','manifestDigest','evaluatorVersion']),
    CHECK (snapshot->>'runId'=run_id::text AND snapshot->>'attemptId'=attempt_id::text),
    CHECK (snapshot->>'evaluatorVersion'=evaluator_version),
    CHECK (snapshot->>'evidenceSetDigest'=evidence_set_digest),
    CHECK (snapshot->>'manifestDigest'=manifest_digest)
);
ALTER TABLE run_evaluation ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_evaluation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON run_evaluation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_run_evaluation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'a retained evaluation is immutable; corrections require a linked run'
          USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM run r JOIN run_attempt a ON a.run_id=r.id
        WHERE r.id=NEW.run_id AND r.workspace_id=NEW.workspace_id AND a.id=NEW.attempt_id
          AND r.status='COMPLETED' AND r.outcome=NEW.outcome
          AND r.manifest_digest=NEW.manifest_digest AND r.lease_epoch=a.lease_epoch) THEN
        RAISE EXCEPTION 'evaluation requires its exact completed run and outcome'
          USING ERRCODE='integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER run_evaluation_is_bound_and_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON run_evaluation FOR EACH ROW EXECUTE FUNCTION guard_run_evaluation();
