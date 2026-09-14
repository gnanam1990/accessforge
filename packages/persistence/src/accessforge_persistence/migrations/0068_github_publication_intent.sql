-- One durable create slot per original run/App/repository, not per disposable preview.
-- No foreign keys to deletable evidence: this minimal tombstone must survive run/preview
-- deletion so deleting a local result cannot silently authorize duplicate remote creation.
CREATE TABLE github_publication_intent (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 app_id BIGINT NOT NULL CHECK(app_id>0),
 repository_id BIGINT NOT NULL CHECK(repository_id>0),
 run_id UUID NOT NULL,
 preview_id UUID NOT NULL,
 approval_id UUID NOT NULL UNIQUE,
 preview_digest TEXT NOT NULL CHECK(preview_digest ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,app_id,repository_id,run_id),
 UNIQUE(workspace_id,preview_id)
);
ALTER TABLE github_publication_intent ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_publication_intent FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON github_publication_intent
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_github_publication_intent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
 THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'publication intent is irreversible; reconcile remote outcome, never redispatch'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER github_publication_intent_guard BEFORE UPDATE OR DELETE
 ON github_publication_intent FOR EACH ROW EXECUTE FUNCTION guard_github_publication_intent();
