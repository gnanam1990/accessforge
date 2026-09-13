-- Historical regression results acquire no invented endpoint provenance.
ALTER TABLE candidate_regression_attempt ADD COLUMN endpoint_required BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE candidate_endpoint (
 attempt_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 plan JSONB NOT NULL CHECK (jsonb_typeof(plan)='object'),
 state TEXT NOT NULL CHECK (state IN ('PLANNED','BOUND','CLOSED','UNKNOWN')),
 origin TEXT,
 receipt JSONB,
 binding_digest TEXT CHECK (binding_digest ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 bound_at TIMESTAMPTZ,
 closed_at TIMESTAMPTZ,
 cleanup_confirmed BOOLEAN NOT NULL DEFAULT false,
 FOREIGN KEY (attempt_id,workspace_id) REFERENCES candidate_regression_attempt(id,workspace_id)
   ON DELETE CASCADE,
 CHECK (expires_at > created_at AND expires_at <= created_at + interval '61 seconds'),
 CHECK ((receipt IS NOT NULL) = (origin IS NOT NULL)),
 CHECK ((receipt IS NOT NULL) = (binding_digest IS NOT NULL)),
 CHECK ((receipt IS NOT NULL) = (bound_at IS NOT NULL)),
 CHECK (receipt IS NULL OR (jsonb_typeof(receipt)='object' AND
   receipt->>'origin'=origin AND receipt->>'bindingDigest'=binding_digest)),
 CHECK (state <> 'BOUND' OR receipt IS NOT NULL),
 CHECK ((closed_at IS NOT NULL) = cleanup_confirmed),
 CHECK (state <> 'CLOSED' OR cleanup_confirmed)
);

CREATE FUNCTION preserve_candidate_endpoint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state='CLOSED' OR
    (to_jsonb(NEW)-ARRAY['state','origin','receipt','binding_digest','bound_at',
                       'closed_at','cleanup_confirmed']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['state','origin','receipt','binding_digest','bound_at',
                       'closed_at','cleanup_confirmed']) OR
    (OLD.receipt IS NOT NULL AND
     ROW(NEW.origin,NEW.receipt,NEW.binding_digest,NEW.bound_at) IS DISTINCT FROM
     ROW(OLD.origin,OLD.receipt,OLD.binding_digest,OLD.bound_at)) OR
    (OLD.cleanup_confirmed AND ROW(NEW.closed_at,NEW.cleanup_confirmed) IS DISTINCT FROM
                              ROW(OLD.closed_at,OLD.cleanup_confirmed)) OR
    NOT ((OLD.state='PLANNED' AND NEW.state IN ('BOUND','CLOSED','UNKNOWN')) OR
         (OLD.state='BOUND' AND NEW.state IN ('CLOSED','UNKNOWN')) OR
         (OLD.state='UNKNOWN' AND NEW.state='UNKNOWN' AND NOT OLD.cleanup_confirmed
                             AND NEW.cleanup_confirmed)) THEN
   RAISE EXCEPTION 'endpoint identity or terminal state is immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF OLD.state<>'PLANNED' AND OLD.receipt IS NULL AND NEW.receipt IS NOT NULL THEN
   RAISE EXCEPTION 'a fenced endpoint cannot acquire a late binding'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_endpoint_immutable BEFORE UPDATE ON candidate_endpoint
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_endpoint();

CREATE FUNCTION regression_endpoint_lifecycle() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state='PASSED' AND NEW.endpoint_required AND NOT EXISTS (
   SELECT 1 FROM candidate_endpoint WHERE attempt_id=NEW.id AND state='CLOSED'
      AND cleanup_confirmed AND receipt IS NOT NULL
 ) THEN
   RAISE EXCEPTION 'required endpoint has no complete binding and cleanup'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.state IN ('PASSED','FAILED') AND EXISTS (
   SELECT 1 FROM candidate_endpoint WHERE attempt_id=NEW.id AND state<>'CLOSED'
 ) THEN
   RAISE EXCEPTION 'endpoint cleanup remains unresolved'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.state='UNKNOWN' THEN
   UPDATE candidate_endpoint SET state='UNKNOWN'
    WHERE attempt_id=NEW.id AND state IN ('PLANNED','BOUND');
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER regression_endpoint_lifecycle AFTER UPDATE ON candidate_regression_attempt
 FOR EACH ROW EXECUTE FUNCTION regression_endpoint_lifecycle();

ALTER TABLE candidate_endpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_endpoint FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_endpoint
 USING (workspace_id=current_workspace_id()) WITH CHECK (workspace_id=current_workspace_id());
COMMENT ON TABLE candidate_endpoint IS
 'Intent commits before binding; exact origin receipt commits before admission. BOUND is not '
 'browser/reader proof. Fencing/restore preserves UNKNOWN even when later cleanup is observed.';
