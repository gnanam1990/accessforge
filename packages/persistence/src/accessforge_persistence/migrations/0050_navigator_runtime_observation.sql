-- Content-free observations from the trusted SDK wrapper, not model-authored assertions.
CREATE TABLE navigator_runtime_observation (
 operation_id UUID NOT NULL,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 observation JSONB NOT NULL CHECK(jsonb_typeof(observation)='object'
   AND octet_length(observation::text)<=8192),
 observation_digest TEXT NOT NULL CHECK(observation_digest ~ '^[0-9a-f]{64}$'),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(operation_id,workspace_id),
 FOREIGN KEY(operation_id,workspace_id) REFERENCES navigator_model_turn(operation_id,workspace_id)
);
ALTER TABLE navigator_runtime_observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE navigator_runtime_observation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON navigator_runtime_observation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_navigator_runtime_observation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'navigator runtime evidence is permanent'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' THEN
   RAISE EXCEPTION 'navigator runtime evidence is immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NOT EXISTS (
   SELECT 1 FROM navigator_model_turn t
   JOIN diagnosis_invocation i ON i.operation_id=t.operation_id AND i.workspace_id=t.workspace_id
   JOIN navigator_model_consent c ON c.id=t.consent_id AND c.workspace_id=t.workspace_id
   WHERE t.operation_id=NEW.operation_id AND t.workspace_id=NEW.workspace_id
     AND i.purpose='NAVIGATOR' AND i.status='STARTED'
     AND NEW.observation->'profile'=c.model_profile
     AND NEW.observation->>'meaning'=
       'SDK_REQUEST_CONFIGURATION_AND_RESPONSE_NOT_PROVIDER_MODEL_ATTESTATION'
   FOR SHARE OF i
 ) THEN
   RAISE EXCEPTION 'runtime evidence requires its exact open admitted model invocation'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER navigator_runtime_observation_guard
 BEFORE INSERT OR UPDATE OR DELETE ON navigator_runtime_observation
 FOR EACH ROW EXECUTE FUNCTION guard_navigator_runtime_observation();

ALTER TABLE evidence_artifact DROP CONSTRAINT evidence_artifact_kind_check;
ALTER TABLE evidence_artifact ADD CONSTRAINT evidence_artifact_kind_check CHECK(kind IN (
 'SPEECH_TRANSCRIPT','ACTION_TRACE','RUNNER_JOURNAL','PREFLIGHT_RECORD','EFFECT_RECEIPT',
 'DIAGNOSTIC_LOG','SCREENSHOT','MODEL_RUNTIME'
));
