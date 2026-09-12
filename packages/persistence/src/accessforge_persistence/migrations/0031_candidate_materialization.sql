-- Captured patched source and retained output, not an invented Git commit or reader outcome.
CREATE TABLE candidate_materialization (
 build_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 source_snapshot_id UUID NOT NULL,
 source_tree_digest TEXT NOT NULL CHECK (source_tree_digest ~ '^[a-f0-9]{64}$'),
 changed_paths TEXT[] NOT NULL,
 build_artifact_id UUID,
 artifact_digest TEXT CHECK (artifact_digest ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 published_at TIMESTAMPTZ,
 FOREIGN KEY (build_id,workspace_id) REFERENCES candidate_build_attempt(id,workspace_id)
   ON DELETE CASCADE,
 FOREIGN KEY (source_snapshot_id,workspace_id) REFERENCES source_snapshot(id,workspace_id)
   ON DELETE CASCADE,
 FOREIGN KEY (build_artifact_id,workspace_id) REFERENCES build_artifact(id,workspace_id)
   ON DELETE CASCADE,
 CHECK ((build_artifact_id IS NOT NULL) = (artifact_digest IS NOT NULL)),
 CHECK ((build_artifact_id IS NOT NULL) = (published_at IS NOT NULL))
);
CREATE FUNCTION preserve_candidate_materialization() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.published_at IS NOT NULL OR NEW.published_at IS NULL OR
    (to_jsonb(NEW)-ARRAY['build_artifact_id','artifact_digest','published_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['build_artifact_id','artifact_digest','published_at']) THEN
   RAISE EXCEPTION 'candidate source and published materialization are immutable'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER candidate_materialization_immutable BEFORE UPDATE ON candidate_materialization
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_materialization();
ALTER TABLE candidate_materialization ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate_materialization FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON candidate_materialization
 USING (workspace_id=current_workspace_id()) WITH CHECK (workspace_id=current_workspace_id());
COMMENT ON TABLE candidate_materialization IS
 'Source captured before execution, output published only from re-read retained bytes. Source commit '
 'is the base, dirty paths describe the approved in-memory patch. Historical builds stay unbound.';
