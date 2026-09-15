-- Confirmation of one exact original create; absence remains UNKNOWN, never retry authority.
ALTER TABLE github_publication_intent ADD CONSTRAINT github_intent_workspace_unique
 UNIQUE(id,workspace_id);
CREATE TABLE github_publication_receipt (
 intent_id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 check_run_id BIGINT NOT NULL CHECK(check_run_id>0),
 payload_digest TEXT NOT NULL CHECK(payload_digest ~ '^[a-f0-9]{64}$'),
 observed_at TIMESTAMPTZ NOT NULL,
 token_revoked BOOLEAN NOT NULL CHECK(token_revoked),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(intent_id,workspace_id) REFERENCES github_publication_intent(id,workspace_id)
   ON DELETE CASCADE
);
ALTER TABLE github_publication_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_publication_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON github_publication_receipt
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE TRIGGER github_publication_receipt_guard BEFORE UPDATE OR DELETE
 ON github_publication_receipt FOR EACH ROW EXECUTE FUNCTION guard_github_publication_intent();
