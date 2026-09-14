CREATE TABLE baseline_regression_attempt (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 build_id UUID NOT NULL UNIQUE,
 run_id UUID NOT NULL UNIQUE,
 worker_token UUID NOT NULL,
 epoch BIGINT NOT NULL DEFAULT 1 CHECK(epoch>=1),
 artifact_digest TEXT NOT NULL CHECK(artifact_digest ~ '^[a-f0-9]{64}$'),
 policy_digest TEXT NOT NULL CHECK(policy_digest ~ '^[a-f0-9]{64}$'),
 image_id TEXT NOT NULL CHECK(image_id ~ '^sha256:[a-f0-9]{64}$'),
 daemon_endpoint TEXT NOT NULL,
 daemon_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('CLAIMED','DISPATCHED','PASSED','FAILED','UNKNOWN')),
 lease_expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 dispatched_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 cleanup_confirmed BOOLEAN NOT NULL DEFAULT false,
 checks TEXT[] NOT NULL DEFAULT '{}',
 validation JSONB,
 failure_code TEXT,
 UNIQUE(id,workspace_id),
 FOREIGN KEY(build_id,workspace_id) REFERENCES baseline_archive(build_id,workspace_id)
  ON DELETE CASCADE,
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 CHECK((state IN ('PASSED','FAILED','UNKNOWN'))=(finished_at IS NOT NULL)),
 CHECK(state<>'PASSED' OR (cleanup_confirmed AND cardinality(checks)>0
  AND dispatched_at IS NOT NULL AND validation IS NOT NULL)),
 CHECK(state<>'FAILED' OR cleanup_confirmed)
);
CREATE TABLE baseline_regression_process (
 LIKE candidate_regression_process INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
 PRIMARY KEY(attempt_id,role),
 UNIQUE(container_name),
 FOREIGN KEY(workspace_id) REFERENCES workspace(id) ON DELETE CASCADE,
 FOREIGN KEY(attempt_id,workspace_id) REFERENCES baseline_regression_attempt(id,workspace_id)
  ON DELETE CASCADE
);
CREATE FUNCTION preserve_baseline_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('PASSED','FAILED','UNKNOWN') OR
   (to_jsonb(NEW)-ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                       'checks','validation','failure_code']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                       'checks','validation','failure_code']) OR
   NOT ((OLD.state='CLAIMED' AND NEW.state IN ('DISPATCHED','FAILED','UNKNOWN')) OR
        (OLD.state='DISPATCHED' AND NEW.state IN ('PASSED','FAILED','UNKNOWN'))) THEN
   RAISE EXCEPTION 'baseline regression identity, transition or terminal receipt is immutable'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_regression_immutable BEFORE UPDATE ON baseline_regression_attempt
 FOR EACH ROW EXECUTE FUNCTION preserve_baseline_regression();
CREATE TRIGGER baseline_regression_process_immutable BEFORE UPDATE ON baseline_regression_process
 FOR EACH ROW EXECUTE FUNCTION preserve_regression_process();
ALTER TABLE baseline_regression_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_regression_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_regression_attempt
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
ALTER TABLE baseline_regression_process ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_regression_process FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_regression_process
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
COMMENT ON TABLE baseline_regression_attempt IS
 'Protected HTTP/database measurements against retained original build bytes. '
 'Not a screen-reader run outcome or repair attestation. No automatic retry after ambiguity.';

CREATE FUNCTION block_unsettled_baseline_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM id FROM run WHERE id=NEW.run_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM baseline_regression_attempt WHERE run_id=NEW.run_id AND state<>'PASSED') THEN
  RAISE EXCEPTION 'baseline protected execution is active, failed or uncertain'
   USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_regression_before_desktop BEFORE INSERT ON desktop_lease
 FOR EACH ROW EXECUTE FUNCTION block_unsettled_baseline_regression();
