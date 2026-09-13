-- Existing seals are input fingerprints, not complete RunManifest execution authority.
-- Do not invent run IDs, approvals, budgets or historical expiry for those records.
ALTER TABLE sealed_manifest ADD COLUMN canonical_manifest JSONB
 CHECK(canonical_manifest IS NULL OR jsonb_typeof(canonical_manifest)='object');
CREATE UNIQUE INDEX canonical_manifest_authorization_once
 ON sealed_manifest((canonical_manifest->>'authorizationId')) WHERE canonical_manifest IS NOT NULL;
CREATE INDEX canonical_manifest_digest_lookup
 ON sealed_manifest(manifest_digest) WHERE canonical_manifest IS NOT NULL;

CREATE FUNCTION guard_canonical_seal_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.canonical_manifest IS NOT NULL AND EXISTS(
   SELECT 1 FROM run r WHERE r.id=NEW.run_id AND (
     r.workspace_id IS DISTINCT FROM NEW.workspace_id OR r.project_id IS DISTINCT FROM NEW.project_id
     OR r.manifest_digest IS DISTINCT FROM NEW.manifest_digest
     OR r.authorization_id IS DISTINCT FROM NEW.authorization_id)) THEN
   RAISE EXCEPTION 'canonical seal cannot replace an existing run identity'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER canonical_seal_identity BEFORE INSERT ON sealed_manifest
 FOR EACH ROW EXECUTE FUNCTION guard_canonical_seal_identity();

CREATE FUNCTION guard_canonical_run_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sealed RECORD;
BEGIN
 FOR sealed IN SELECT * FROM sealed_manifest m WHERE m.canonical_manifest IS NOT NULL
   AND (m.run_id=NEW.id OR m.manifest_digest=NEW.manifest_digest
        OR (TG_OP='UPDATE' AND m.manifest_digest=OLD.manifest_digest)) LOOP
   IF NEW.id IS DISTINCT FROM sealed.run_id
      OR NEW.workspace_id IS DISTINCT FROM sealed.workspace_id
      OR NEW.project_id IS DISTINCT FROM sealed.project_id
      OR NEW.manifest_digest IS DISTINCT FROM sealed.manifest_digest
      OR NEW.authorization_id IS DISTINCT FROM sealed.authorization_id THEN
     RAISE EXCEPTION 'canonical run identity is immutable; retry needs a new run and authorization'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
 END LOOP;
 RETURN NEW;
END;
$$;
CREATE TRIGGER canonical_run_identity BEFORE INSERT OR UPDATE ON run
 FOR EACH ROW EXECUTE FUNCTION guard_canonical_run_identity();
COMMENT ON COLUMN sealed_manifest.canonical_manifest IS
 'Complete schema-validated RunManifest, including reserved exact run/approval identity and budgets. '
 'NULL is an older input fingerprint, never execution authority. Reservation is not approval.';
