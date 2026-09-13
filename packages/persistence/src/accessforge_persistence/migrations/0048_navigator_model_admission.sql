-- Shared model ledger: navigator holds include every configured provider retry attempt.
ALTER TABLE diagnosis_invocation DROP CONSTRAINT diagnosis_invocation_purpose_check;
ALTER TABLE diagnosis_invocation ADD CONSTRAINT diagnosis_invocation_purpose_check
 CHECK(purpose IN ('DIAGNOSIS','REPAIR','NAVIGATOR'));
ALTER TABLE diagnosis_invocation DROP CONSTRAINT diagnosis_invocation_reserved_tokens_check;
ALTER TABLE diagnosis_invocation ADD CONSTRAINT diagnosis_invocation_reserved_tokens_check
 CHECK(reserved_tokens BETWEEN 1 AND CASE WHEN purpose='NAVIGATOR' THEN 150000 ELSE 50000 END);

CREATE TABLE navigator_model_consent (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 run_id UUID NOT NULL UNIQUE,
 actor_user UUID NOT NULL,
 manifest_digest TEXT NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'),
 model_config_digest TEXT NOT NULL CHECK(model_config_digest ~ '^[0-9a-f]{64}$'),
 model_profile JSONB NOT NULL CHECK(jsonb_typeof(model_profile)='object'
   AND octet_length(model_profile::text)<=4096),
 max_calls INTEGER NOT NULL CHECK(max_calls BETWEEN 1 AND 500),
 tokens_per_call INTEGER NOT NULL CHECK(tokens_per_call BETWEEN 1 AND 150000),
 billable_call_acknowledged BOOLEAN NOT NULL CHECK(billable_call_acknowledged),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at>created_at),
 revoked_at TIMESTAMPTZ,
 UNIQUE(id,run_id,workspace_id),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE navigator_model_consent ENABLE ROW LEVEL SECURITY;
ALTER TABLE navigator_model_consent FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON navigator_model_consent
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_navigator_model_consent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM run r JOIN workspace w ON w.id=r.workspace_id
             WHERE r.id=OLD.run_id AND r.workspace_id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'navigator consent cannot be deleted and reissued'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at') OR
    (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
   RAISE EXCEPTION 'navigator model consent and revocation are immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER navigator_model_consent_guard BEFORE UPDATE OR DELETE ON navigator_model_consent
 FOR EACH ROW EXECUTE FUNCTION guard_navigator_model_consent();

CREATE TABLE navigator_model_turn (
 operation_id UUID NOT NULL,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 run_id UUID NOT NULL,
 attempt_id UUID NOT NULL,
 runner_id UUID NOT NULL,
 lease_id UUID NOT NULL,
 lease_epoch BIGINT NOT NULL CHECK(lease_epoch>0),
 consent_id UUID NOT NULL,
 action_sequence INTEGER NOT NULL CHECK(action_sequence BETWEEN 0 AND 499),
 projection_digest TEXT NOT NULL CHECK(projection_digest ~ '^[0-9a-f]{64}$'),
 reader_records_digest TEXT NOT NULL CHECK(reader_records_digest ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(operation_id,workspace_id),
 UNIQUE(attempt_id,action_sequence),
 FOREIGN KEY(operation_id,workspace_id) REFERENCES diagnosis_invocation(operation_id,workspace_id),
 FOREIGN KEY(consent_id,run_id,workspace_id) REFERENCES navigator_model_consent(id,run_id,workspace_id),
 FOREIGN KEY(attempt_id,run_id,workspace_id) REFERENCES run_attempt(id,run_id,workspace_id),
 FOREIGN KEY(runner_id,workspace_id) REFERENCES runner(id,workspace_id),
 FOREIGN KEY(lease_id,workspace_id) REFERENCES desktop_lease(id,workspace_id)
);
ALTER TABLE navigator_model_turn ENABLE ROW LEVEL SECURITY;
ALTER TABLE navigator_model_turn FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON navigator_model_turn
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_navigator_model_turn() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
   IF NOT EXISTS(
     SELECT 1 FROM diagnosis_invocation i JOIN desktop_lease l ON l.id=NEW.lease_id
       AND l.workspace_id=NEW.workspace_id
     WHERE i.operation_id=NEW.operation_id AND i.workspace_id=NEW.workspace_id
       AND i.run_id=NEW.run_id AND i.purpose='NAVIGATOR' AND i.status='STARTED'
       AND l.run_id=NEW.run_id AND l.attempt_id=NEW.attempt_id
       AND l.runner_id=NEW.runner_id AND l.epoch=NEW.lease_epoch
   ) THEN
     RAISE EXCEPTION 'navigator turn requires its own reservation and exact desktop attempt'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN NEW;
 END IF;
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
   RETURN OLD;
 END IF;
 RAISE EXCEPTION 'navigator turn identity is permanent; an uncertain call is never replayed'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER navigator_model_turn_guard BEFORE INSERT OR UPDATE OR DELETE ON navigator_model_turn
 FOR EACH ROW EXECUTE FUNCTION guard_navigator_model_turn();
