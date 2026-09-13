-- An initial receiver admission is consumed once, not replayed after response loss.
-- This authenticates dispatch reception; it is not actual-reader or per-action evidence.
CREATE TABLE supervisor_dispatch_ticket (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 run_id UUID NOT NULL,
 attempt_id UUID NOT NULL,
 runner_id UUID NOT NULL,
 lease_id UUID NOT NULL,
 epoch BIGINT NOT NULL CHECK(epoch > 0),
 token_digest TEXT NOT NULL UNIQUE CHECK(token_digest ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL CHECK(expires_at > created_at),
 accepted_at TIMESTAMPTZ,
 revoked_at TIMESTAMPTZ,
 UNIQUE(run_id, workspace_id),
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(attempt_id,workspace_id) REFERENCES run_attempt(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(runner_id,workspace_id) REFERENCES runner(id,workspace_id),
 FOREIGN KEY(lease_id,workspace_id) REFERENCES desktop_lease(id,workspace_id)
);
ALTER TABLE supervisor_dispatch_ticket ENABLE ROW LEVEL SECURITY;
ALTER TABLE supervisor_dispatch_ticket FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON supervisor_dispatch_ticket
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_supervisor_dispatch_ticket() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'retained dispatch tickets cannot be deleted and reissued'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' AND (
     (to_jsonb(NEW)-ARRAY['accepted_at','revoked_at']) IS DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['accepted_at','revoked_at'])
     OR (OLD.accepted_at IS NOT NULL AND NEW.accepted_at IS DISTINCT FROM OLD.accepted_at)
     OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)) THEN
   RAISE EXCEPTION 'dispatch ticket identity and consumption are irreversible'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF TG_OP='INSERT' AND NOT EXISTS(
   SELECT 1 FROM desktop_lease l JOIN run_attempt a
     ON a.id=l.attempt_id AND a.workspace_id=l.workspace_id
   WHERE l.id=NEW.lease_id AND l.workspace_id=NEW.workspace_id AND l.run_id=NEW.run_id
     AND l.runner_id=NEW.runner_id AND l.attempt_id=NEW.attempt_id AND l.epoch=NEW.epoch
     AND a.run_id=NEW.run_id AND a.lease_epoch=NEW.epoch
 ) THEN
   RAISE EXCEPTION 'dispatch ticket must bind one exact leased attempt'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER supervisor_dispatch_ticket_immutable BEFORE INSERT OR UPDATE OR DELETE
 ON supervisor_dispatch_ticket FOR EACH ROW EXECUTE FUNCTION guard_supervisor_dispatch_ticket();
