-- Tester Desk M06: redacted client reports bound to audience, release and revision (INV-06).
-- A report revision is an immutable projection of a sealed submission: only consented evidence
-- enters it, and every revision carries its own digest so an export cannot drift.

CREATE TABLE td_report_revision (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    client_id UUID NOT NULL,
    commission_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    submission_id UUID NOT NULL,
    release_label TEXT NOT NULL,
    release_digest TEXT NOT NULL,
    revision INT NOT NULL CHECK (revision > 0),
    audience TEXT NOT NULL DEFAULT 'CLIENT' CHECK (audience IN ('CLIENT')),
    body JSONB NOT NULL,
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[0-9a-f]{64}$'),
    generated_by UUID NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (assignment_id, revision),
    UNIQUE (id, workspace_id),
    FOREIGN KEY (client_id, workspace_id) REFERENCES td_client(id, workspace_id),
    FOREIGN KEY (commission_id, workspace_id)
        REFERENCES td_commission(id, workspace_id),
    FOREIGN KEY (assignment_id, workspace_id)
        REFERENCES td_assignment(id, workspace_id),
    FOREIGN KEY (submission_id, workspace_id)
        REFERENCES td_submission(id, workspace_id)
);
ALTER TABLE td_report_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_report_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_report_revision
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_report_revision_client
    ON td_report_revision (workspace_id, client_id, release_label);

CREATE FUNCTION guard_td_report_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'report revisions are immutable'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER td_report_revision_guard BEFORE UPDATE OR DELETE ON td_report_revision
    FOR EACH ROW EXECUTE FUNCTION guard_td_report_revision();
