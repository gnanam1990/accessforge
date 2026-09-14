-- Captured bytes are not retained artifacts or evidence of a reader run.
CREATE TABLE baseline_build_attempt (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 run_id UUID NOT NULL UNIQUE,
 worker_token UUID NOT NULL,
 epoch BIGINT NOT NULL DEFAULT 1 CHECK(epoch>=1),
 binding JSONB NOT NULL CHECK(jsonb_typeof(binding)='object'),
 source_archive_digest TEXT NOT NULL CHECK(source_archive_digest ~ '^[a-f0-9]{64}$'),
 policy_digest TEXT NOT NULL CHECK(policy_digest ~ '^[a-f0-9]{64}$'),
 image_id TEXT NOT NULL CHECK(image_id ~ '^sha256:[a-f0-9]{64}$'),
 daemon_endpoint TEXT NOT NULL,
 daemon_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('CLAIMED','DISPATCHED','CAPTURED','FAILED','UNKNOWN')),
 lease_expires_at TIMESTAMPTZ NOT NULL,
 container_id TEXT CHECK(container_id ~ '^[a-f0-9]{64}$'),
 platform TEXT,
 artifact_digest TEXT CHECK(artifact_digest ~ '^[a-f0-9]{64}$'),
 cleanup_confirmed BOOLEAN NOT NULL DEFAULT false,
 failure_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ,
 FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id) ON DELETE CASCADE,
 CHECK(binding->>'run_id'=run_id::text AND binding->>'workspace_id'=workspace_id::text),
 CHECK((state IN ('CAPTURED','FAILED','UNKNOWN'))=(finished_at IS NOT NULL)),
 CHECK(state<>'CLAIMED' OR container_id IS NULL),
 CHECK((container_id IS NULL)=(platform IS NULL)),
 CHECK(state<>'CAPTURED' OR (container_id IS NOT NULL AND cleanup_confirmed
   AND artifact_digest IS NOT NULL AND artifact_digest=binding->>'expected_artifact_digest')),
 CHECK(state<>'FAILED' OR cleanup_confirmed)
);
ALTER TABLE baseline_build_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_build_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON baseline_build_attempt
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION preserve_baseline_build() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state=NEW.state AND
    (to_jsonb(NEW)-ARRAY['container_id','platform']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['container_id','platform']) THEN
   RAISE EXCEPTION 'baseline creation may only record process identity'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF OLD.state IN ('CAPTURED','FAILED','UNKNOWN') OR
    (to_jsonb(NEW)-ARRAY['state','epoch','container_id','platform','artifact_digest',
       'cleanup_confirmed','failure_code','finished_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['state','epoch','container_id','platform','artifact_digest',
       'cleanup_confirmed','failure_code','finished_at']) OR
    (OLD.container_id IS NOT NULL AND
      (NEW.container_id IS DISTINCT FROM OLD.container_id OR NEW.platform IS DISTINCT FROM OLD.platform)) THEN
   RAISE EXCEPTION 'baseline inputs, process identity or terminal receipt are immutable'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 IF NOT ((OLD.state='CLAIMED' AND NEW.state IN ('DISPATCHED','FAILED','UNKNOWN')) OR
         (OLD.state='DISPATCHED' AND NEW.state IN ('CAPTURED','FAILED','UNKNOWN')) OR
         (OLD.state='DISPATCHED' AND NEW.state='DISPATCHED' AND OLD.container_id IS NULL
          AND NEW.container_id IS NOT NULL)) THEN
   RAISE EXCEPTION 'invalid baseline build transition' USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_build_immutable BEFORE UPDATE ON baseline_build_attempt
 FOR EACH ROW EXECUTE FUNCTION preserve_baseline_build();

CREATE FUNCTION block_unsettled_baseline_build() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 -- Serialize against claim/dispatch, which locks the same run before inspecting leases.
 PERFORM id FROM run WHERE id=NEW.run_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM baseline_build_attempt WHERE run_id=NEW.run_id AND state<>'CAPTURED') THEN
   RAISE EXCEPTION 'baseline build is failed, active or uncertain; desktop dispatch refused'
    USING ERRCODE='integrity_constraint_violation';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER baseline_build_before_desktop BEFORE INSERT ON desktop_lease
 FOR EACH ROW EXECUTE FUNCTION block_unsettled_baseline_build();
