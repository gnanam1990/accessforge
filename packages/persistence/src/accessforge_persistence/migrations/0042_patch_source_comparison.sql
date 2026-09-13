-- Trusted broker-derived original source; never an upload accepted from the patch author.
CREATE TABLE patch_source_comparison (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 patch_id UUID NOT NULL,
 project_id UUID NOT NULL,
 source_snapshot_id UUID NOT NULL,
 patch_digest TEXT NOT NULL CHECK(patch_digest ~ '^[0-9a-f]{64}$'),
 base_source_digest TEXT NOT NULL CHECK(base_source_digest ~ '^[0-9a-f]{64}$'),
 comparison_digest TEXT NOT NULL CHECK(comparison_digest ~ '^[0-9a-f]{64}$'),
 payload JSONB CHECK(payload IS NULL OR (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16777216)),
 prepared_by UUID NOT NULL REFERENCES app_user(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 retired_at TIMESTAMPTZ,
 CHECK((payload IS NULL)=(retired_at IS NOT NULL)),
 UNIQUE(patch_id,patch_digest,base_source_digest),
 FOREIGN KEY(patch_id,workspace_id) REFERENCES patch_proposal(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,workspace_id) REFERENCES project(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(source_snapshot_id,workspace_id) REFERENCES source_snapshot(id,workspace_id) ON DELETE CASCADE
);
ALTER TABLE patch_source_comparison ENABLE ROW LEVEL SECURITY;
ALTER TABLE patch_source_comparison FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON patch_source_comparison
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_patch_source_comparison() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND (NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
    OR NOT EXISTS(SELECT 1 FROM patch_proposal WHERE id=OLD.patch_id)
    OR NOT EXISTS(SELECT 1 FROM source_snapshot WHERE id=OLD.source_snapshot_id)
    OR NOT EXISTS(SELECT 1 FROM project WHERE id=OLD.project_id)) THEN RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL AND NEW.payload IS NULL
    AND (to_jsonb(NEW)-ARRAY['payload','retired_at'])=(to_jsonb(OLD)-ARRAY['payload','retired_at']) THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'source comparison is immutable except permanent source retirement'
  USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER patch_source_comparison_guard BEFORE UPDATE OR DELETE ON patch_source_comparison
 FOR EACH ROW EXECUTE FUNCTION guard_patch_source_comparison();
CREATE FUNCTION retire_revoked_project_comparisons() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.revoked_at IS NOT NULL OR NEW.repository_authorized_by IS NULL THEN
  UPDATE patch_source_comparison SET payload=NULL,retired_at=clock_timestamp()
   WHERE project_id=NEW.id AND retired_at IS NULL;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER project_comparisons_retired AFTER UPDATE OF revoked_at,repository_authorized_by ON project
 FOR EACH ROW EXECUTE FUNCTION retire_revoked_project_comparisons();
