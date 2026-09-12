-- Protected regressions are a separately fenced execution, not a boolean supplied by a client.
CREATE TABLE candidate_regression_attempt (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 build_id UUID NOT NULL UNIQUE,
 worker_token UUID NOT NULL,
 epoch BIGINT NOT NULL DEFAULT 1 CHECK (epoch >= 1),
 artifact_digest TEXT NOT NULL CHECK (artifact_digest ~ '^[a-f0-9]{64}$'),
 policy_digest TEXT NOT NULL CHECK (policy_digest ~ '^[a-f0-9]{64}$'),
 image_id TEXT NOT NULL CHECK (image_id ~ '^sha256:[a-f0-9]{64}$'),
 daemon_endpoint TEXT NOT NULL,
 daemon_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('CLAIMED','DISPATCHED','PASSED','FAILED','UNKNOWN')),
 lease_expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 dispatched_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 cleanup_confirmed BOOLEAN NOT NULL DEFAULT false,
 checks TEXT[] NOT NULL DEFAULT '{}',
 failure_code TEXT,
 FOREIGN KEY (build_id,workspace_id) REFERENCES candidate_build_attempt(id,workspace_id)
   ON DELETE CASCADE,
 UNIQUE(id,workspace_id),
 CHECK ((state IN ('PASSED','FAILED','UNKNOWN')) = (finished_at IS NOT NULL)),
 CHECK (state <> 'PASSED' OR
   (cleanup_confirmed AND cardinality(checks) > 0 AND dispatched_at IS NOT NULL)),
 CHECK (state <> 'FAILED' OR cleanup_confirmed)
);

CREATE TABLE candidate_regression_process (
 attempt_id UUID NOT NULL,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK (role IN ('database','driver','candidate','restarted-candidate')),
 container_name TEXT NOT NULL UNIQUE,
 image_id TEXT NOT NULL CHECK (image_id ~ '^sha256:[a-f0-9]{64}$'),
 container_id TEXT CHECK (container_id ~ '^[a-f0-9]{64}$'),
 state TEXT NOT NULL CHECK (state IN ('PLANNED','CREATED','REMOVED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 removed_at TIMESTAMPTZ,
 PRIMARY KEY(attempt_id,role),
 FOREIGN KEY(attempt_id,workspace_id) REFERENCES candidate_regression_attempt(id,workspace_id)
   ON DELETE CASCADE,
 CHECK ((state = 'REMOVED') = (removed_at IS NOT NULL)),
 CHECK (state = 'PLANNED' OR container_id IS NOT NULL)
);

CREATE FUNCTION preserve_candidate_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('PASSED','FAILED','UNKNOWN') OR
   (to_jsonb(NEW) - ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                        'checks','failure_code']) IS DISTINCT FROM
   (to_jsonb(OLD) - ARRAY['state','epoch','dispatched_at','finished_at','cleanup_confirmed',
                        'checks','failure_code']) THEN
   RAISE EXCEPTION 'regression input or terminal record is immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NOT ((OLD.state='CLAIMED' AND NEW.state IN ('DISPATCHED','FAILED','UNKNOWN')) OR
         (OLD.state='DISPATCHED' AND NEW.state IN ('PASSED','FAILED','UNKNOWN'))) THEN
   RAISE EXCEPTION 'invalid regression transition' USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_regression_immutable BEFORE UPDATE ON candidate_regression_attempt
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_regression();
CREATE FUNCTION preserve_regression_process() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW) - ARRAY['state','container_id','removed_at']) IS DISTINCT FROM
    (to_jsonb(OLD) - ARRAY['state','container_id','removed_at']) OR
    (OLD.container_id IS NOT NULL AND NEW.container_id IS DISTINCT FROM OLD.container_id) OR
    NOT ((OLD.state='PLANNED' AND NEW.state='CREATED') OR
         (OLD.state='CREATED' AND NEW.state='REMOVED')) THEN
   RAISE EXCEPTION 'regression process identity or transition is immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_regression_process_immutable BEFORE UPDATE ON candidate_regression_process
 FOR EACH ROW EXECUTE FUNCTION preserve_regression_process();

ALTER TABLE candidate_regression_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_regression_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_regression_attempt
 USING (workspace_id=current_workspace_id()) WITH CHECK (workspace_id=current_workspace_id());
ALTER TABLE candidate_regression_process ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_regression_process FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_regression_process
 USING (workspace_id=current_workspace_id()) WITH CHECK (workspace_id=current_workspace_id());
COMMENT ON TABLE candidate_regression_attempt IS
 'One trusted local execution per retained build. PASSED is not VERIFIED or actual-reader evidence. '
 'Expiry/restore is UNKNOWN; deterministic role names and immutable receipts permit reconciliation.';
