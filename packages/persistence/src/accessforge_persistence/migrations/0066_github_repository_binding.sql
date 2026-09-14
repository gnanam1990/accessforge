-- A local allowlist, not GitHub publication authority or a cached access grant.
CREATE TABLE github_repository_binding (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 app_id BIGINT NOT NULL CHECK(app_id > 0),
 installation_id BIGINT NOT NULL CHECK(installation_id > 0),
 account_id BIGINT NOT NULL CHECK(account_id > 0),
 repository_id BIGINT NOT NULL CHECK(repository_id > 0),
 owner_name TEXT NOT NULL CHECK(owner_name ~ '^[A-Za-z0-9_.-]{1,100}$'
   AND owner_name NOT IN ('.','..')),
 repository_name TEXT NOT NULL CHECK(repository_name ~ '^[A-Za-z0-9_.-]{1,100}$'
   AND repository_name NOT IN ('.','..')),
 observed_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 revoked_at TIMESTAMPTZ,
 CHECK(observed_at <= created_at AND observed_at >= created_at - INTERVAL '30 seconds'),
 CHECK(revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE UNIQUE INDEX github_repository_binding_live
 ON github_repository_binding(workspace_id,app_id,repository_id) WHERE revoked_at IS NULL;
ALTER TABLE github_repository_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_repository_binding FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON github_repository_binding
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_github_repository_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
 THEN RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
   AND (to_jsonb(NEW)-'revoked_at') = (to_jsonb(OLD)-'revoked_at')
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'repository binding is immutable except one-way revocation'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER github_repository_binding_guard
 BEFORE UPDATE OR DELETE ON github_repository_binding
 FOR EACH ROW EXECUTE FUNCTION guard_github_repository_binding();
