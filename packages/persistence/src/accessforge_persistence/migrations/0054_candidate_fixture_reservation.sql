-- Commit nonce intent before the isolated application can create its browser fixture.
-- This is build-worker history, not RUN_EFFECTS approval or a desktop observation.
CREATE TABLE candidate_fixture_reservation (
 regression_attempt_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 context JSONB NOT NULL CHECK(jsonb_typeof(context)='object' AND octet_length(context::text)<=8192),
 context_digest TEXT NOT NULL CHECK(context_digest ~ '^[0-9a-f]{64}$'),
 observation JSONB CHECK(jsonb_typeof(observation)='object' AND octet_length(observation::text)<=8192),
 observation_digest TEXT CHECK(observation_digest ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 observed_at TIMESTAMPTZ,
 CHECK ((observation IS NULL AND observation_digest IS NULL AND observed_at IS NULL)
     OR (observation IS NOT NULL AND observation_digest IS NOT NULL AND observed_at IS NOT NULL)),
 FOREIGN KEY(regression_attempt_id,workspace_id)
   REFERENCES candidate_regression_attempt(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE candidate_fixture_reservation ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_fixture_reservation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_fixture_reservation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_candidate_fixture_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'candidate fixture reservation cannot be erased'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 PERFORM id FROM candidate_regression_attempt
   WHERE id=NEW.regression_attempt_id AND workspace_id=NEW.workspace_id
     AND state='DISPATCHED' AND lease_expires_at>clock_timestamp() AND endpoint_required
   FOR UPDATE;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM candidate_endpoint
                        WHERE attempt_id=NEW.regression_attempt_id) THEN
   RAISE EXCEPTION 'candidate fixture setup requires a live pre-endpoint worker'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF TG_OP='INSERT' AND NEW.observation IS NOT NULL THEN
   RAISE EXCEPTION 'candidate fixture must first reserve an unconfirmed context'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF TG_OP='UPDATE' AND (
     OLD.observation IS NOT NULL OR NEW.observation IS NULL
     OR (to_jsonb(NEW)-'observation'-'observation_digest'-'observed_at')
        IS DISTINCT FROM (to_jsonb(OLD)-'observation'-'observation_digest'-'observed_at')) THEN
   RAISE EXCEPTION 'candidate fixture context and confirmed observation are immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_fixture_reservation_guard
 BEFORE INSERT OR UPDATE OR DELETE ON candidate_fixture_reservation
 FOR EACH ROW EXECUTE FUNCTION guard_candidate_fixture_reservation();
