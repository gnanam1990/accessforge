-- Tester Desk M04: sealed submission revisions and digest-bound review decisions.
-- A submission is an immutable snapshot of the draft observations plus the
-- attachment manifest; the review decision is bound to the submission digest so
-- approval against an obsolete revision is a conflict, not a stale accept.

CREATE TABLE td_submission (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    revision INT NOT NULL CHECK (revision > 0),
    submission_hash TEXT NOT NULL CHECK (submission_hash ~ '^[0-9a-f]{64}$'),
    task_result TEXT NOT NULL CHECK (task_result IN
        ('PASSED','FAILED','INCONCLUSIVE')),
    observations JSONB NOT NULL,
    attachment_manifest JSONB NOT NULL,
    sealed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (assignment_id, revision),
    UNIQUE (id, workspace_id),
    FOREIGN KEY (assignment_id, workspace_id)
        REFERENCES td_assignment(id, workspace_id)
);
ALTER TABLE td_submission ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_submission FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_submission
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- Sealed submissions are the evidence of record: they cannot be rewritten or removed.
CREATE FUNCTION guard_td_submission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'sealed submissions are immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER td_submission_guard BEFORE UPDATE OR DELETE ON td_submission
    FOR EACH ROW EXECUTE FUNCTION guard_td_submission();

CREATE TABLE td_review_decision (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    submission_id UUID NOT NULL,
    submission_hash TEXT NOT NULL,
    assignment_id UUID NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ACCEPTED','NEEDS_REVISION')),
    justification TEXT NOT NULL,
    decided_by UUID NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- One decision per sealed submission: a revision request ends that submission's
    -- review, and acceptance is terminal for it.
    UNIQUE (submission_id),
    UNIQUE (id, workspace_id),
    FOREIGN KEY (submission_id, workspace_id)
        REFERENCES td_submission(id, workspace_id)
);
ALTER TABLE td_review_decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_review_decision FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_review_decision
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_review_decision_assignment
    ON td_review_decision (workspace_id, assignment_id);
