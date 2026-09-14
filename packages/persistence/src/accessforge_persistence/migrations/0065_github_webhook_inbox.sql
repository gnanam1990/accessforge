-- Authentication receipts only. These rows do not authorize execution/publication.
CREATE TABLE github_webhook_body (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 app_id BIGINT NOT NULL CHECK(app_id > 0),
 body_digest TEXT NOT NULL CHECK(body_digest ~ '^[a-f0-9]{64}$'),
 installation_id BIGINT NOT NULL CHECK(installation_id > 0),
 repository_id BIGINT CHECK(repository_id > 0),
 received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id, app_id, body_digest),
 UNIQUE(id, workspace_id, app_id)
);
CREATE TABLE github_webhook_delivery (
 workspace_id UUID NOT NULL,
 app_id BIGINT NOT NULL,
 delivery_id UUID NOT NULL,
 body_id UUID NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id, app_id, delivery_id),
 FOREIGN KEY(body_id, workspace_id, app_id)
   REFERENCES github_webhook_body(id, workspace_id, app_id) ON DELETE CASCADE
);
ALTER TABLE github_webhook_body ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_webhook_body FORCE ROW LEVEL SECURITY;
ALTER TABLE github_webhook_delivery ENABLE ROW LEVEL SECURITY;
ALTER TABLE github_webhook_delivery FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON github_webhook_body
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE POLICY workspace_isolation ON github_webhook_delivery
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_github_webhook_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
 THEN RETURN OLD; END IF;
 RAISE EXCEPTION 'webhook authentication receipts are immutable'
   USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER github_webhook_body_guard BEFORE UPDATE OR DELETE ON github_webhook_body
 FOR EACH ROW EXECUTE FUNCTION guard_github_webhook_receipt();
CREATE TRIGGER github_webhook_delivery_guard BEFORE UPDATE OR DELETE ON github_webhook_delivery
 FOR EACH ROW EXECUTE FUNCTION guard_github_webhook_receipt();
