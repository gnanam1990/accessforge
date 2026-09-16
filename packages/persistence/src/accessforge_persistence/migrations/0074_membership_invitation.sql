-- Invitation IDs are references, not bearer credentials. Acceptance also requires a verified
-- immutable GitHub subject and a live binding to an enabled local account.
CREATE TABLE membership_invitation (
    workspace_id UUID NOT NULL REFERENCES workspace(id),
    id UUID NOT NULL,
    github_subject BIGINT NOT NULL CHECK (github_subject > 0),
    role TEXT NOT NULL CHECK (role IN ('OWNER','MAINTAINER','REVIEWER','VIEWER')),
    created_by UUID NOT NULL REFERENCES app_user(id),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    revoked_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    accepted_by UUID REFERENCES app_user(id),
    PRIMARY KEY (workspace_id,id),
    CHECK (expires_at > created_at),
    CHECK ((accepted_at IS NULL) = (accepted_by IS NULL)),
    CHECK (accepted_at IS NULL OR revoked_at IS NULL)
);
ALTER TABLE membership_invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE membership_invitation FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_invitation_workspace ON membership_invitation
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX membership_invitation_pending ON membership_invitation(workspace_id,github_subject)
    WHERE accepted_at IS NULL AND revoked_at IS NULL;
