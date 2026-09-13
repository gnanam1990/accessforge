CREATE TABLE repair_delivery (
 request_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 request_digest TEXT NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
 input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
 binding_digest TEXT NOT NULL CHECK(binding_digest ~ '^[0-9a-f]{64}$'),
 outcome TEXT NOT NULL CHECK(outcome IN ('PROPOSED','NO_PROPOSAL')),
 patch_id UUID,
 patch_digest TEXT CHECK(patch_digest ~ '^[0-9a-f]{64}$'),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(request_id,workspace_id) REFERENCES repair_request(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(patch_id,workspace_id) REFERENCES patch_proposal(id,workspace_id),
 CHECK((outcome='PROPOSED')=(patch_id IS NOT NULL)),
 CHECK((patch_id IS NULL)=(patch_digest IS NULL))
);
ALTER TABLE repair_delivery ENABLE ROW LEVEL SECURITY;
ALTER TABLE repair_delivery FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON repair_delivery
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_repair_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM repair_request WHERE id=OLD.request_id) THEN
  RETURN OLD;
 END IF;
 IF TG_OP='INSERT' AND EXISTS(
   SELECT 1 FROM repair_request q JOIN diagnosis_invocation i
     ON i.operation_id=q.id AND i.workspace_id=q.workspace_id AND i.purpose='REPAIR'
   WHERE q.id=NEW.request_id AND q.workspace_id=NEW.workspace_id
     AND i.request_digest=NEW.request_digest AND i.status='STARTED'
     AND q.revoked_at IS NULL AND q.expires_at>clock_timestamp()
     AND (NEW.patch_id IS NULL OR EXISTS(
       SELECT 1 FROM patch_proposal p WHERE p.id=NEW.patch_id AND p.workspace_id=q.workspace_id
         AND p.finding_id=q.finding_id AND p.patch_digest=NEW.patch_digest AND p.status='PROPOSED'
         AND p.base_manifest_digest=q.payload->>'manifestDigest'
         AND p.base_source_digest=q.payload->>'sourceTreeDigest'
     ))
 ) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'repair delivery requires exact active invocation and immutable proposal binding'
  USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER repair_delivery_guard BEFORE INSERT OR UPDATE OR DELETE ON repair_delivery
 FOR EACH ROW EXECUTE FUNCTION guard_repair_delivery();
