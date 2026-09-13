-- Human request for one bounded model operation. No worker runs merely because it is inserted.
CREATE TABLE diagnosis_request (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    run_id UUID NOT NULL,
    requested_by UUID NOT NULL REFERENCES app_user(id),
    payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=32768),
    payload_digest TEXT NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '1 hour'),
    revoked_at TIMESTAMPTZ,
    UNIQUE(id,workspace_id),
    FOREIGN KEY(run_id,workspace_id) REFERENCES run(id,workspace_id),
    CHECK(expires_at>created_at AND expires_at<=created_at+interval '61 minutes')
);
ALTER TABLE diagnosis_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE diagnosis_request FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON diagnosis_request
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE INDEX diagnosis_request_run ON diagnosis_request(workspace_id,run_id,created_at);
CREATE FUNCTION guard_diagnosis_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' AND NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id) THEN
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
       AND (to_jsonb(NEW)-'revoked_at')=(to_jsonb(OLD)-'revoked_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'diagnosis request is immutable except permanent revocation'
      USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER diagnosis_request_guard BEFORE UPDATE OR DELETE ON diagnosis_request
 FOR EACH ROW EXECUTE FUNCTION guard_diagnosis_request();
