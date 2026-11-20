-- Tester Desk M03: observation drafts and digest-backed evidence attachments.
-- Drafts belong to exactly one assignment and one tester; attachments are
-- content-addressed (sha256) rows, never external URLs, so evidence replays
-- from persisted state alone (NFR-01).

CREATE TABLE td_observation_draft (
    assignment_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    tester_id UUID NOT NULL,
    observations JSONB NOT NULL DEFAULT '[]',
    draft_revision INT NOT NULL CHECK (draft_revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (assignment_id, workspace_id),
    FOREIGN KEY (assignment_id, workspace_id)
        REFERENCES td_assignment(id, workspace_id),
    FOREIGN KEY (tester_id, workspace_id) REFERENCES td_tester(id, workspace_id)
);
ALTER TABLE td_observation_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_observation_draft FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_observation_draft
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE TABLE td_evidence_attachment (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    tester_id UUID NOT NULL,
    kind TEXT NOT NULL,
    content_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    byte_size INT NOT NULL CHECK (byte_size > 0 AND byte_size <= 8388608),
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, workspace_id),
    UNIQUE (assignment_id, sha256),
    FOREIGN KEY (assignment_id, workspace_id)
        REFERENCES td_assignment(id, workspace_id),
    FOREIGN KEY (tester_id, workspace_id) REFERENCES td_tester(id, workspace_id)
);
ALTER TABLE td_evidence_attachment ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_evidence_attachment FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_evidence_attachment
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_evidence_attachment_assignment
    ON td_evidence_attachment (workspace_id, assignment_id);
