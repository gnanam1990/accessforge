-- Immutable original model analysis, separate from findings, machine outcomes and human review.
CREATE TABLE diagnosis_invocation (
    operation_id UUID NOT NULL,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    run_id UUID NOT NULL,
    request_digest TEXT NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
    reserved_tokens BIGINT NOT NULL CHECK(reserved_tokens BETWEEN 1 AND 50000),
    status TEXT NOT NULL DEFAULT 'STARTED' CHECK(status IN ('STARTED','RECORDED','UNCONFIRMED','NOT_CALLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY(operation_id,workspace_id),
    FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id),
    CHECK ((status='STARTED')=(finished_at IS NULL))
);
ALTER TABLE diagnosis_invocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE diagnosis_invocation FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON diagnosis_invocation
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_diagnosis_invocation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND OLD.status='STARTED' AND NEW.status<>'STARTED'
       AND NEW.finished_at IS NOT NULL
       AND (to_jsonb(NEW)-'status'-'finished_at')=(to_jsonb(OLD)-'status'-'finished_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'diagnosis invocation identity and final disposition are immutable'
      USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER diagnosis_invocation_guard BEFORE UPDATE OR DELETE ON diagnosis_invocation
 FOR EACH ROW EXECUTE FUNCTION guard_diagnosis_invocation();

CREATE TABLE finding_diagnosis (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    finding_id UUID NOT NULL,
    run_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    operation_id UUID NOT NULL,
    request_digest TEXT NOT NULL CHECK(request_digest ~ '^[0-9a-f]{64}$'),
    requested_by UUID NOT NULL,
    group_digest TEXT NOT NULL CHECK(group_digest ~ '^[0-9a-f]{64}$'),
    evaluation_digest TEXT NOT NULL CHECK(evaluation_digest ~ '^[0-9a-f]{64}$'),
    projection_digest TEXT NOT NULL CHECK(projection_digest ~ '^[0-9a-f]{64}$'),
    model_profile_digest TEXT NOT NULL CHECK(model_profile_digest ~ '^[0-9a-f]{64}$'),
    payload_digest TEXT NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
    payload JSONB CHECK(payload IS NULL OR (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536)),
    supersedes UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    CHECK ((payload IS NULL)=(deleted_at IS NOT NULL)),
    UNIQUE(id,finding_id,workspace_id),
    UNIQUE(operation_id,workspace_id),
    UNIQUE(supersedes),
    FOREIGN KEY(finding_id,workspace_id) REFERENCES finding(id,workspace_id) ON DELETE CASCADE,
    FOREIGN KEY(attempt_id,run_id,workspace_id) REFERENCES run_attempt(id,run_id,workspace_id),
    FOREIGN KEY(supersedes,finding_id,workspace_id) REFERENCES finding_diagnosis(id,finding_id,workspace_id)
);
CREATE UNIQUE INDEX finding_diagnosis_one_original ON finding_diagnosis(finding_id) WHERE supersedes IS NULL;
CREATE UNIQUE INDEX finding_diagnosis_one_occurrence ON finding_diagnosis(workspace_id,run_id,group_digest) WHERE supersedes IS NULL;
CREATE INDEX finding_diagnosis_group ON finding_diagnosis(workspace_id,group_digest,created_at);
ALTER TABLE finding_diagnosis ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_diagnosis FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON finding_diagnosis
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());

CREATE FUNCTION guard_finding_diagnosis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        IF EXISTS(SELECT 1 FROM finding WHERE id=OLD.finding_id) THEN
            RAISE EXCEPTION 'retained diagnosis cannot be deleted' USING ERRCODE='integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.payload IS NOT NULL AND NEW.payload IS NULL AND NEW.deleted_at IS NOT NULL
           AND (to_jsonb(NEW)-'payload'-'deleted_at')=(to_jsonb(OLD)-'payload'-'deleted_at')
           AND EXISTS(SELECT 1 FROM evidence_artifact WHERE attempt_id=OLD.attempt_id AND retention='DELETED') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'diagnosis revisions are immutable except one-way retention erasure'
          USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.payload IS NULL OR NOT EXISTS(
        SELECT 1 FROM run_evaluation e JOIN finding f ON f.run_id=e.run_id AND f.workspace_id=e.workspace_id
        WHERE f.id=NEW.finding_id AND e.run_id=NEW.run_id AND e.attempt_id=NEW.attempt_id
          AND e.workspace_id=NEW.workspace_id AND e.snapshot_digest=NEW.evaluation_digest
          AND e.outcome IN ('FAIL','INCONCLUSIVE')
    ) OR EXISTS(SELECT 1 FROM evidence_artifact WHERE attempt_id=NEW.attempt_id AND retention='DELETED') THEN
        RAISE EXCEPTION 'diagnosis requires its exact retained evaluation' USING ERRCODE='integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER finding_diagnosis_guard BEFORE INSERT OR UPDATE OR DELETE ON finding_diagnosis
 FOR EACH ROW EXECUTE FUNCTION guard_finding_diagnosis();

-- Any source-artifact erasure also erases model-derived text; keep only provenance tombstones.
CREATE FUNCTION erase_derived_diagnosis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.retention='DELETED' AND OLD.retention IS DISTINCT FROM 'DELETED' THEN
        UPDATE finding_diagnosis SET payload=NULL,deleted_at=clock_timestamp()
         WHERE attempt_id=NEW.attempt_id AND workspace_id=NEW.workspace_id AND payload IS NOT NULL;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER evidence_erases_derived_diagnosis AFTER UPDATE OF retention ON evidence_artifact
 FOR EACH ROW EXECUTE FUNCTION erase_derived_diagnosis();
