-- Extend receipt admission without rewriting any historical receipt, digest or consent.
-- CLI completion is not HTTP response evidence; only its exact provider/meaning pair is new.
CREATE OR REPLACE FUNCTION guard_navigator_runtime_observation() RETURNS trigger LANGUAGE plpgsql AS $$
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
     AND (
       (c.model_profile->>'provider'='amazon-bedrock' AND NEW.observation->>'meaning'=
         'SDK_REQUEST_CONFIGURATION_AND_RESPONSE_NOT_PROVIDER_MODEL_ATTESTATION')
       OR (c.model_profile->>'provider'='codex-chatgpt' AND NEW.observation->>'meaning'=
         'CLI_CONFIGURATION_AND_COMPLETION_NOT_PROVIDER_MODEL_ATTESTATION')
     )
   FOR SHARE OF i
 ) THEN
   RAISE EXCEPTION 'runtime evidence requires its exact open admitted model invocation'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
