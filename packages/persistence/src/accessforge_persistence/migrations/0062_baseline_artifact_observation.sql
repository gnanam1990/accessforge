-- Build receipts are not canonical supervisor/observer events or run verdicts.
CREATE TABLE baseline_artifact_observation (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 regression_attempt_id UUID NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 256),
 receipt_digest TEXT NOT NULL CHECK(receipt_digest ~ '^[a-f0-9]{64}$'),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<8192),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(regression_attempt_id,ordinal), UNIQUE(regression_attempt_id,receipt_digest),
 FOREIGN KEY(regression_attempt_id,workspace_id)
   REFERENCES baseline_regression_attempt(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE baseline_artifact_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_artifact_observation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_artifact_observation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_baseline_artifact_observation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(
   SELECT 1 FROM baseline_regression_attempt WHERE id=OLD.regression_attempt_id
 ) THEN RETURN OLD; END IF;
 IF TG_OP='INSERT' AND EXISTS(
   SELECT 1 FROM baseline_regression_attempt a JOIN baseline_endpoint e ON e.attempt_id=a.id
   WHERE a.id=NEW.regression_attempt_id AND a.workspace_id=NEW.workspace_id
     AND a.state='DISPATCHED' AND a.lease_expires_at>clock_timestamp()
     AND e.state='BOUND' AND e.expires_at>clock_timestamp()
 ) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'artifact observation requires a live endpoint and immutable receipt'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER baseline_artifact_observation_guard
 BEFORE INSERT OR UPDATE OR DELETE ON baseline_artifact_observation
 FOR EACH ROW EXECUTE FUNCTION guard_baseline_artifact_observation();
