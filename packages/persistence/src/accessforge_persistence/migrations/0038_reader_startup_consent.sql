-- Separate human infrastructure consent for SDK startup effects. Not RUN_EFFECTS or TCC authority.
CREATE TABLE reader_startup_consent (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 run_id UUID NOT NULL UNIQUE,
 runner_id UUID NOT NULL,
 actor_user UUID NOT NULL,
 manifest_digest TEXT NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'),
 desktop_session_key TEXT NOT NULL CHECK(desktop_session_key ~ '^[0-9a-f]{64}$'),
 runner_profile_digest TEXT NOT NULL CHECK(runner_profile_digest ~ '^[0-9a-f]{64}$'),
 effects_digest TEXT NOT NULL CHECK(effects_digest ~ '^[0-9a-f]{64}$'),
 dedicated_desktop_acknowledged BOOLEAN NOT NULL CHECK(dedicated_desktop_acknowledged),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at>created_at),
 revoked_at TIMESTAMPTZ,
 bound_session_id UUID,
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(runner_id,workspace_id) REFERENCES runner(id,workspace_id),
 FOREIGN KEY(bound_session_id,workspace_id)
   REFERENCES supervisor_dispatch_ticket(id,workspace_id)
);
ALTER TABLE reader_startup_consent ENABLE ROW LEVEL SECURITY;
ALTER TABLE reader_startup_consent FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON reader_startup_consent
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_reader_startup_consent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-'revoked_at'-'bound_session_id') IS DISTINCT FROM
    (to_jsonb(OLD)-'revoked_at'-'bound_session_id') OR
    (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) OR
    (OLD.bound_session_id IS NOT NULL AND NEW.bound_session_id IS DISTINCT FROM OLD.bound_session_id)
 THEN
   RAISE EXCEPTION 'reader startup consent identity, binding and revocation are irreversible'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.bound_session_id IS DISTINCT FROM OLD.bound_session_id AND NOT EXISTS(
   SELECT 1 FROM supervisor_execution_session s JOIN supervisor_dispatch_ticket t ON t.id=s.ticket_id
   WHERE s.ticket_id=NEW.bound_session_id AND s.workspace_id=NEW.workspace_id
     AND t.run_id=NEW.run_id AND t.runner_id=NEW.runner_id
     AND t.revoked_at IS NULL AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
 ) THEN
   RAISE EXCEPTION 'reader consent requires an exact live execution session'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER reader_startup_consent_immutable BEFORE UPDATE ON reader_startup_consent
 FOR EACH ROW EXECUTE FUNCTION guard_reader_startup_consent();
