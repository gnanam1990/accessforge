ALTER TABLE baseline_regression_attempt ADD COLUMN endpoint_required BOOLEAN NOT NULL DEFAULT false;
CREATE TABLE baseline_endpoint (
 LIKE candidate_endpoint INCLUDING DEFAULTS INCLUDING CONSTRAINTS,
 PRIMARY KEY(attempt_id),
 FOREIGN KEY(workspace_id) REFERENCES workspace(id) ON DELETE CASCADE,
 FOREIGN KEY(attempt_id,workspace_id) REFERENCES baseline_regression_attempt(id,workspace_id)
  ON DELETE CASCADE
);
CREATE TRIGGER baseline_endpoint_immutable BEFORE UPDATE ON baseline_endpoint
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_endpoint();
ALTER TABLE baseline_endpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_endpoint FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_endpoint
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION baseline_endpoint_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='PASSED' AND NEW.endpoint_required AND NOT EXISTS(
  SELECT 1 FROM baseline_endpoint WHERE attempt_id=NEW.id AND state='CLOSED'
   AND cleanup_confirmed AND receipt IS NOT NULL
 ) THEN
  RAISE EXCEPTION 'baseline endpoint lacks original binding and cleanup'
   USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.state IN ('PASSED','FAILED') AND EXISTS(
  SELECT 1 FROM baseline_endpoint WHERE attempt_id=NEW.id AND state<>'CLOSED'
 ) THEN
  RAISE EXCEPTION 'baseline endpoint cleanup remains unresolved'
   USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.state='UNKNOWN' THEN
  UPDATE baseline_endpoint SET state='UNKNOWN'
   WHERE attempt_id=NEW.id AND state IN ('PLANNED','BOUND');
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_endpoint_lifecycle AFTER UPDATE ON baseline_regression_attempt
 FOR EACH ROW EXECUTE FUNCTION baseline_endpoint_lifecycle();
COMMENT ON TABLE baseline_endpoint IS
 'Original approved origin/fixture listener lifecycle. BOUND is not a reader lease or verdict. '
 'Parent expiry/restore fences live endpoints; late cleanup cannot revive UNKNOWN.';
