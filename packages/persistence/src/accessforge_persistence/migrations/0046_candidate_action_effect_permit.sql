-- Per-action control-plane permission, never proof of a physical action or application effect.
ALTER TABLE runner_action ADD CONSTRAINT runner_action_id_workspace_unique UNIQUE(id,workspace_id);
CREATE TABLE candidate_action_effect_permit (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 action_id UUID NOT NULL,
 regression_attempt_id UUID NOT NULL,
 preflight_event_id UUID NOT NULL REFERENCES canonical_event(event_id) ON DELETE CASCADE,
 grant_digest TEXT NOT NULL CHECK(grant_digest ~ '^[a-f0-9]{64}$'),
 grant_payload JSONB NOT NULL CHECK(jsonb_typeof(grant_payload)='object'),
 granted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 consumed_at TIMESTAMPTZ,
 UNIQUE(action_id,workspace_id),
 CHECK(expires_at>granted_at AND expires_at<=granted_at+interval '5 seconds'),
 CHECK(consumed_at IS NULL OR (consumed_at>=granted_at AND consumed_at<expires_at)),
 FOREIGN KEY(action_id,workspace_id) REFERENCES runner_action(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(regression_attempt_id,workspace_id)
  REFERENCES candidate_regression_attempt(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE candidate_action_effect_permit ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_action_effect_permit FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_action_effect_permit
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_candidate_action_effect_permit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND (NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
   OR NOT EXISTS(SELECT 1 FROM canonical_event WHERE event_id=OLD.preflight_event_id)
   OR NOT EXISTS(SELECT 1 FROM runner_action WHERE id=OLD.action_id)
   OR NOT EXISTS(SELECT 1 FROM candidate_regression_attempt WHERE id=OLD.regression_attempt_id))
 THEN RETURN OLD; END IF;
 IF TG_OP='INSERT' AND NEW.consumed_at IS NULL AND EXISTS(
   SELECT 1 FROM runner_action WHERE id=NEW.action_id AND workspace_id=NEW.workspace_id
    AND dispatched_at IS NOT NULL AND result_at IS NULL
    AND (action='ACTIVATE' OR (action='KEY_CHORD' AND key_chord IN ('ENTER','SPACE')))
 ) THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL
   AND to_jsonb(NEW)-'consumed_at'=to_jsonb(OLD)-'consumed_at'
   AND NEW.expires_at>clock_timestamp()
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'candidate action permit is immutable and cannot be rearmed'
  USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER candidate_action_effect_permit_guard
 BEFORE INSERT OR UPDATE OR DELETE ON candidate_action_effect_permit
 FOR EACH ROW EXECUTE FUNCTION guard_candidate_action_effect_permit();
