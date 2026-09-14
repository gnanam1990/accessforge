-- Original captured baseline output, separate from transcript evidence and candidate repairs.
ALTER TABLE baseline_build_attempt ADD UNIQUE(id,workspace_id);
CREATE TABLE baseline_archive (
 build_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 content_digest TEXT NOT NULL CHECK(content_digest ~ '^[a-f0-9]{64}$'),
 size_bytes BIGINT NOT NULL CHECK(size_bytes>0 AND size_bytes<=41943040),
 object_key TEXT NOT NULL UNIQUE,
 store_endpoint TEXT NOT NULL,
 store_bucket TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('QUARANTINED','RETAINED','DELETED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 retained_at TIMESTAMPTZ,
 deleted_at TIMESTAMPTZ,
 UNIQUE(build_id,workspace_id),
 FOREIGN KEY(build_id,workspace_id) REFERENCES baseline_build_attempt(id,workspace_id)
  ON DELETE CASCADE,
 CHECK((state='RETAINED')=(retained_at IS NOT NULL AND deleted_at IS NULL)),
 CHECK((state='DELETED')=(deleted_at IS NOT NULL))
);
CREATE FUNCTION preserve_baseline_archive() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['state','retained_at','deleted_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['state','retained_at','deleted_at']) OR
    NOT ((OLD.state='QUARANTINED' AND NEW.state IN ('RETAINED','DELETED')) OR
         (OLD.state='RETAINED' AND NEW.state='DELETED')) OR
    (OLD.retained_at IS NOT NULL AND NEW.retained_at IS DISTINCT FROM OLD.retained_at) THEN
   RAISE EXCEPTION 'baseline archive identity or terminal retirement is immutable'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_archive_immutable BEFORE UPDATE ON baseline_archive
 FOR EACH ROW EXECUTE FUNCTION preserve_baseline_archive();
ALTER TABLE baseline_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_archive FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_archive
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE TABLE baseline_archive_retirement (
 build_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 policy_revision BIGINT NOT NULL CHECK(policy_revision>=0),
 retain_days BIGINT NOT NULL CHECK(retain_days>=0),
 requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 completed_at TIMESTAMPTZ,
 FOREIGN KEY(build_id,workspace_id) REFERENCES baseline_archive(build_id,workspace_id)
  ON DELETE CASCADE
);
CREATE TRIGGER baseline_retirement_immutable BEFORE UPDATE ON baseline_archive_retirement
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_retirement();
ALTER TABLE baseline_archive_retirement ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_archive_retirement FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_archive_retirement
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE TABLE baseline_archive_restore_location (
 build_id UUID NOT NULL,
 workspace_id UUID NOT NULL,
 revision BIGINT NOT NULL CHECK(revision>0),
 restore_id TEXT NOT NULL,
 store_endpoint TEXT NOT NULL,
 store_bucket TEXT NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(build_id,revision),
 UNIQUE(build_id,restore_id),
 FOREIGN KEY(build_id,workspace_id) REFERENCES baseline_archive(build_id,workspace_id)
  ON DELETE CASCADE
);
CREATE TRIGGER baseline_restore_location_immutable BEFORE UPDATE ON baseline_archive_restore_location
 FOR EACH ROW EXECUTE FUNCTION preserve_candidate_restore_location();
ALTER TABLE baseline_archive_restore_location ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_archive_restore_location FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_archive_restore_location
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
COMMENT ON TABLE baseline_archive IS
 'Create-only executable archives, unavailable until bounded read-back and fresh original '
 'approval checks. Retained bytes do not establish protected regressions or reader evidence.';
