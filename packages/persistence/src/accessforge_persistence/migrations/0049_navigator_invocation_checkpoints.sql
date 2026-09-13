-- Existing unbound planning history is retained as legacy, not relabelled as an admitted call.
ALTER TABLE navigator_planning_checkpoint ADD COLUMN operation_id UUID;
ALTER TABLE navigator_planning_checkpoint ADD CONSTRAINT navigator_checkpoint_operation_fk
 FOREIGN KEY(operation_id,workspace_id)
 REFERENCES navigator_model_turn(operation_id,workspace_id);

CREATE UNIQUE INDEX navigator_checkpoint_one_kind_per_invocation
 ON navigator_planning_checkpoint(workspace_id,operation_id,kind)
 WHERE operation_id IS NOT NULL;

CREATE FUNCTION guard_bound_navigator_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   IF OLD.operation_id IS NOT NULL AND
      EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
     RAISE EXCEPTION 'admitted navigator checkpoints are permanent'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN OLD;
 END IF;
 IF TG_OP='UPDATE' THEN
   IF OLD.operation_id IS NOT NULL OR NEW.operation_id IS NOT NULL THEN
     RAISE EXCEPTION 'admitted navigator checkpoints cannot be rewritten or rebound'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
   RETURN NEW;
 END IF;
 IF NEW.operation_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM navigator_model_turn t
   JOIN diagnosis_invocation i ON i.operation_id=t.operation_id
     AND i.workspace_id=t.workspace_id
   JOIN navigator_model_consent c ON c.id=t.consent_id AND c.workspace_id=t.workspace_id
   WHERE t.operation_id=NEW.operation_id AND t.workspace_id=NEW.workspace_id
     AND t.run_id=NEW.run_id AND t.attempt_id=NEW.attempt_id
     AND i.purpose='NAVIGATOR' AND i.status='STARTED'
     AND (NEW.model_id IS NULL OR (
       NEW.model_id=c.model_profile->>'model_id'
       AND NEW.provider=c.model_profile->>'provider'
       AND NEW.sdk_version=c.model_profile->>'sdk_version'))
   FOR SHARE OF i
 ) THEN
   RAISE EXCEPTION 'checkpoint requires its exact open admitted navigator invocation'
     USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NEW.operation_id IS NOT NULL AND NEW.kind='ACTION_RESOLVED' THEN
   IF (NEW.dispatch_status IN ('SUCCEEDED','FAILED') AND NEW.action_id IS NULL) OR
      (NEW.action_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM navigator_model_turn t JOIN runner_action a
          ON a.id=NEW.action_id AND a.workspace_id=t.workspace_id
        WHERE t.operation_id=NEW.operation_id AND t.workspace_id=NEW.workspace_id
          AND a.run_id=t.run_id AND a.attempt_id=t.attempt_id
          AND a.lease_id=t.lease_id AND a.epoch=t.lease_epoch
          AND a.action_sequence=t.action_sequence+1 AND a.action=NEW.action
          AND (NEW.dispatch_status NOT IN ('SUCCEEDED','FAILED') OR (
            a.result_status=NEW.dispatch_status AND a.result_at IS NOT NULL
            AND a.dispatched_at IS NOT NULL))
      )) THEN
     RAISE EXCEPTION 'resolved checkpoint requires its original native action result'
       USING ERRCODE='integrity_constraint_violation';
   END IF;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER navigator_bound_checkpoint_guard
 BEFORE INSERT OR UPDATE OR DELETE ON navigator_planning_checkpoint
 FOR EACH ROW EXECUTE FUNCTION guard_bound_navigator_checkpoint();
