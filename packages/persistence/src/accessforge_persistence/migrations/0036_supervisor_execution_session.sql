-- A bootstrap ticket is consumed once. Continuing machine authentication is a different secret,
-- scoped to that exact accepted ticket and bounded by its execution lease/consent/manifest.
ALTER TABLE supervisor_dispatch_ticket ADD CONSTRAINT dispatch_ticket_workspace_identity
 UNIQUE(id,workspace_id);

CREATE TABLE supervisor_execution_session (
 ticket_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 token_digest TEXT NOT NULL UNIQUE CHECK(token_digest ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at>created_at),
 revoked_at TIMESTAMPTZ,
 FOREIGN KEY(ticket_id,workspace_id) REFERENCES supervisor_dispatch_ticket(id,workspace_id)
 ON DELETE CASCADE
);
ALTER TABLE supervisor_execution_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE supervisor_execution_session FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON supervisor_execution_session
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_supervisor_execution_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'retained supervisor sessions cannot be deleted and reissued'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' AND (
   (to_jsonb(NEW)-'revoked_at') IS DISTINCT FROM (to_jsonb(OLD)-'revoked_at') OR
   (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)) THEN
   RAISE EXCEPTION 'supervisor session identity and revocation are irreversible'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF TG_OP='INSERT' AND NOT EXISTS(
   SELECT 1 FROM supervisor_dispatch_ticket t WHERE t.id=NEW.ticket_id
     AND t.workspace_id=NEW.workspace_id AND t.accepted_at IS NOT NULL AND t.revoked_at IS NULL
 ) THEN
   RAISE EXCEPTION 'supervisor session requires an accepted exact dispatch ticket'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER supervisor_execution_session_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON supervisor_execution_session FOR EACH ROW EXECUTE FUNCTION guard_supervisor_execution_session();
