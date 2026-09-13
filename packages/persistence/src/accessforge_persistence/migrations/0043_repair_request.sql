-- One budget ledger across model purposes; existing rows retain their diagnosis identity.
ALTER TABLE diagnosis_invocation ADD COLUMN purpose TEXT NOT NULL DEFAULT 'DIAGNOSIS'
 CHECK(purpose IN ('DIAGNOSIS','REPAIR'));

CREATE TABLE repair_request (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
 finding_id UUID NOT NULL,
 diagnosis_id UUID NOT NULL,
 requested_by UUID NOT NULL REFERENCES app_user(id),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=32768),
 payload_digest TEXT NOT NULL CHECK(payload_digest ~ '^[0-9a-f]{64}$'),
 operation_key_digest TEXT NOT NULL CHECK(operation_key_digest ~ '^[0-9a-f]{64}$'),
 supersedes UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '1 hour'),
 revoked_at TIMESTAMPTZ,
 UNIQUE(id,workspace_id),
 UNIQUE(id,finding_id,workspace_id),
 UNIQUE(supersedes),
 UNIQUE(workspace_id,requested_by,finding_id,operation_key_digest),
 FOREIGN KEY(finding_id,workspace_id) REFERENCES finding(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(diagnosis_id,finding_id,workspace_id) REFERENCES finding_diagnosis(id,finding_id,workspace_id),
 FOREIGN KEY(supersedes,finding_id,workspace_id) REFERENCES repair_request(id,finding_id,workspace_id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '61 minutes')
);
CREATE UNIQUE INDEX repair_request_one_original ON repair_request(workspace_id,finding_id)
 WHERE supersedes IS NULL;
ALTER TABLE repair_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE repair_request FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON repair_request
 USING(workspace_id=current_workspace_id()) WITH CHECK(workspace_id=current_workspace_id());
CREATE FUNCTION guard_repair_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND (NOT EXISTS(SELECT 1 FROM workspace WHERE id=OLD.workspace_id)
    OR NOT EXISTS(SELECT 1 FROM finding WHERE id=OLD.finding_id)) THEN RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
    AND (to_jsonb(NEW)-'revoked_at')=(to_jsonb(OLD)-'revoked_at') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'repair request is immutable except permanent revocation'
  USING ERRCODE='integrity_constraint_violation';
END;
$$;
CREATE TRIGGER repair_request_guard BEFORE UPDATE OR DELETE ON repair_request
 FOR EACH ROW EXECUTE FUNCTION guard_repair_request();
