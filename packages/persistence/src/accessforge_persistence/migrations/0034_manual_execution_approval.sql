-- A seal is an immutable approval target. Operational run/lease revisions are not its revision.
-- This initial revision does not backfill consent: legacy seals still lack canonical payloads.
ALTER TABLE sealed_manifest ADD COLUMN authorization_revision BIGINT NOT NULL DEFAULT 0
 CHECK (authorization_revision=0);

CREATE FUNCTION guard_exact_approval_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sealed RECORD;
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) AND EXISTS(
       SELECT 1 FROM sealed_manifest WHERE canonical_manifest IS NOT NULL
       AND authorization_id=OLD.id) THEN
     RAISE EXCEPTION 'execution approval cannot be deleted and reissued for a retained seal'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' THEN
   IF (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at')
      OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
     RAISE EXCEPTION 'exact approval identity is immutable and revocation is irreversible'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN NEW;
 END IF;
 SELECT * INTO sealed FROM sealed_manifest
   WHERE canonical_manifest IS NOT NULL AND authorization_id=NEW.id;
 IF FOUND AND (NEW.workspace_id IS DISTINCT FROM sealed.workspace_id
     OR NEW.scope <> 'RUN_EFFECTS' OR NEW.target_id IS DISTINCT FROM sealed.id
     OR NEW.target_digest IS DISTINCT FROM sealed.manifest_digest
     OR NEW.expected_revision IS DISTINCT FROM sealed.authorization_revision
     OR NEW.expires_at > (sealed.canonical_manifest->>'expiresAt')::timestamptz) THEN
   RAISE EXCEPTION 'reserved execution approval must match its exact canonical seal'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER exact_approval_record BEFORE INSERT OR UPDATE OR DELETE ON approval
 FOR EACH ROW EXECUTE FUNCTION guard_exact_approval_record();
