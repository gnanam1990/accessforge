-- A consumed permission without a retained response is unresolved, never safe to replay.
ALTER TABLE candidate_action_effect_permit ADD CONSTRAINT candidate_effect_permit_id_workspace_unique
 UNIQUE(id,workspace_id);
CREATE TABLE candidate_effect_delivery (
 permit_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 request_digest TEXT NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'),
 claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 response_digest TEXT CHECK(response_digest ~ '^[a-f0-9]{64}$'),
 response_status INTEGER CHECK(response_status IN (200,201,404,409,422)),
 responded_at TIMESTAMPTZ,
 CHECK((response_digest IS NULL)=(response_status IS NULL)),
 CHECK((response_digest IS NULL)=(responded_at IS NULL)),
 CHECK(responded_at IS NULL OR responded_at>=claimed_at),
 FOREIGN KEY(permit_id,workspace_id) REFERENCES candidate_action_effect_permit(id,workspace_id)
  ON DELETE CASCADE
);
ALTER TABLE candidate_effect_delivery ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_effect_delivery FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_effect_delivery
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_candidate_effect_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND (NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
   OR NOT EXISTS(SELECT 1 FROM candidate_action_effect_permit WHERE id=OLD.permit_id))
 THEN RETURN OLD; END IF;
 IF TG_OP='INSERT' AND NEW.response_digest IS NULL AND EXISTS(
   SELECT 1 FROM candidate_action_effect_permit WHERE id=NEW.permit_id
    AND workspace_id=NEW.workspace_id AND consumed_at IS NOT NULL AND expires_at>clock_timestamp()
 ) THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND OLD.response_digest IS NULL AND NEW.response_digest IS NOT NULL
   AND to_jsonb(NEW)-ARRAY['response_digest','response_status','responded_at']
      =to_jsonb(OLD)-ARRAY['response_digest','response_status','responded_at']
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'candidate effect delivery cannot be erased, replayed or replaced'
  USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER candidate_effect_delivery_guard BEFORE INSERT OR UPDATE OR DELETE
 ON candidate_effect_delivery FOR EACH ROW EXECUTE FUNCTION guard_candidate_effect_delivery();
