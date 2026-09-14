ALTER TABLE baseline_endpoint ADD UNIQUE(attempt_id,workspace_id);
CREATE TABLE baseline_session_binding (
 run_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 regression_attempt_id UUID NOT NULL UNIQUE,
 manifest_digest TEXT NOT NULL CHECK(manifest_digest ~ '^[a-f0-9]{64}$'),
 endpoint_binding_digest TEXT NOT NULL CHECK(endpoint_binding_digest ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,workspace_id),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(regression_attempt_id,workspace_id) REFERENCES baseline_endpoint(attempt_id,workspace_id)
  ON DELETE CASCADE
);
CREATE TABLE baseline_reader_lease (
 run_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 lease_id UUID NOT NULL UNIQUE,
 lease_epoch BIGINT NOT NULL CHECK(lease_epoch>=1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,workspace_id) REFERENCES baseline_session_binding(run_id,workspace_id)
  ON DELETE CASCADE,
 FOREIGN KEY(lease_id,workspace_id) REFERENCES desktop_lease(id,workspace_id) ON DELETE CASCADE
);
CREATE TRIGGER baseline_session_binding_immutable BEFORE UPDATE ON baseline_session_binding
 FOR EACH ROW EXECUTE FUNCTION refuse_candidate_binding_mutation();
CREATE TRIGGER baseline_reader_lease_immutable BEFORE UPDATE ON baseline_reader_lease
 FOR EACH ROW EXECUTE FUNCTION refuse_candidate_binding_mutation();
ALTER TABLE baseline_session_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_session_binding FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_session_binding
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
ALTER TABLE baseline_reader_lease ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_reader_lease FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_reader_lease
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE OR REPLACE FUNCTION block_unsettled_baseline_regression() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM id FROM run WHERE id=NEW.run_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM baseline_session_binding WHERE run_id=NEW.run_id) THEN
  IF EXISTS(SELECT 1 FROM baseline_reader_lease WHERE run_id=NEW.run_id) OR NOT EXISTS(
   SELECT 1 FROM baseline_session_binding s
   JOIN run r ON r.id=s.run_id AND r.workspace_id=s.workspace_id
   JOIN sealed_manifest m ON m.run_id=r.id AND m.workspace_id=r.workspace_id
   JOIN baseline_regression_attempt a ON a.id=s.regression_attempt_id AND a.run_id=r.id
   JOIN baseline_endpoint e ON e.attempt_id=a.id AND e.workspace_id=s.workspace_id
   JOIN runner d ON d.id=NEW.runner_id AND d.workspace_id=s.workspace_id
   WHERE s.run_id=NEW.run_id AND s.workspace_id=NEW.workspace_id
    AND s.manifest_digest=r.manifest_digest AND m.manifest_digest=r.manifest_digest
    AND r.status='QUEUED' AND r.cancel_requested_at IS NULL AND NOT r.quarantined
    AND a.state='DISPATCHED' AND a.endpoint_required
    AND e.state='BOUND' AND e.binding_digest=s.endpoint_binding_digest
    AND d.profile_digest=m.runner_profile_digest AND d.revoked_at IS NULL
    AND d.quarantined_at IS NULL AND d.session_key=NEW.session_key
    AND NEW.deadline_at>clock_timestamp()
    AND NEW.deadline_at<=e.expires_at AND NEW.deadline_at<=a.lease_expires_at
    AND NEW.deadline_at<=(m.canonical_manifest->>'expiresAt')::timestamptz
  ) THEN
   RAISE EXCEPTION 'baseline reader lease differs from its original session or lifetime'
    USING ERRCODE='integrity_constraint_violation';
  END IF;
 ELSIF EXISTS(SELECT 1 FROM baseline_regression_attempt WHERE run_id=NEW.run_id AND state<>'PASSED') THEN
  RAISE EXCEPTION 'baseline protected execution is active, failed or uncertain'
   USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE FUNCTION bind_baseline_reader_lease() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM baseline_session_binding WHERE run_id=NEW.run_id) THEN
  INSERT INTO baseline_reader_lease(run_id,workspace_id,lease_id,lease_epoch)
   VALUES(NEW.run_id,NEW.workspace_id,NEW.id,NEW.epoch);
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_reader_lease_bound AFTER INSERT ON desktop_lease
 FOR EACH ROW EXECUTE FUNCTION bind_baseline_reader_lease();
