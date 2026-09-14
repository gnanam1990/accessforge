-- Immutable local intent preview. Existence never authorizes a remote write.
ALTER TABLE github_repository_binding ADD CONSTRAINT github_binding_workspace_key
 UNIQUE(id,workspace_id);
CREATE TABLE github_publication_preview (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 binding_id UUID NOT NULL,
 run_id UUID NOT NULL,
 preview_digest TEXT NOT NULL CHECK(preview_digest ~ '^[a-f0-9]{64}$'),
 preview JSONB NOT NULL CHECK(jsonb_typeof(preview)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(binding_id,workspace_id) REFERENCES github_repository_binding(id,workspace_id),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 CHECK((preview->>'previewDigest'=preview_digest) IS TRUE),
 CHECK((preview->>'kind'='GITHUB_CHECK_CREATE_PREVIEW') IS TRUE),
 CHECK((preview->'identity'->>'workspaceId'=workspace_id::text) IS TRUE),
 CHECK((preview->'identity'->>'bindingId'=binding_id::text) IS TRUE),
 CHECK((preview->'identity'->>'runId'=run_id::text) IS TRUE),
 CHECK(preview ?& ARRAY['previewDigest','kind','identity']),
 CHECK(preview->'identity' ?& ARRAY['workspaceId','bindingId','runId'])
);
ALTER TABLE github_publication_preview ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_publication_preview FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON github_publication_preview
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_github_publication_preview() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND (
   NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
   OR NOT EXISTS(SELECT 1 FROM run WHERE id=OLD.run_id AND workspace_id=OLD.workspace_id)
 ) THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'publication preview is immutable; create a new exact preview'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER github_publication_preview_guard
 BEFORE UPDATE OR DELETE ON github_publication_preview
 FOR EACH ROW EXECUTE FUNCTION guard_github_publication_preview();
