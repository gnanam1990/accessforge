-- Tester Desk enrollment and assignment claims.

-- The roster is agency-controlled enrollment, not workspace membership. A tester holds a row here
-- keyed to their user account; the row carries the declared technology capabilities a claim is
-- matched against and the payout reference compensation later needs. Scoped visibility (FR-02)
-- means an unenrolled person cannot even see that offers exist.
CREATE TABLE td_tester (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES app_user(id),
    display_name TEXT NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 200),
    browser_name TEXT NOT NULL CHECK (char_length(browser_name) BETWEEN 1 AND 100),
    browser_version TEXT NOT NULL CHECK (char_length(browser_version) BETWEEN 1 AND 100),
    assistive_technology TEXT NOT NULL CHECK (char_length(assistive_technology) BETWEEN 1 AND 100),
    -- Opaque handle the compensation adapter resolves later. Required before claiming paid work:
    -- a payable cannot name a payee it never knew.
    payout_reference TEXT,
    -- Declared conflicts of interest (commission references or free-text declarations). A wallet
    -- or account alone cannot establish independence; declarations are the record.
    conflicts JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conflicts) = 'array'
        AND octet_length(conflicts::text) <= 8192),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED','WITHDRAWN')),
    enrolled_by UUID NOT NULL REFERENCES app_user(id),
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status_changed_at TIMESTAMPTZ,
    UNIQUE (workspace_id, user_id),
    UNIQUE (id, workspace_id)
);
ALTER TABLE td_tester ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_tester FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_tester
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- A claim binds the tester to the exact brief revision that was open when they accepted (INV-01).
-- consent_at + consent_terms_version record *what* they agreed to, not just that they agreed.
CREATE TABLE td_assignment (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL,
    commission_id UUID NOT NULL,
    brief_revision INT NOT NULL,
    tester_id UUID NOT NULL,
    consent_terms_version INT NOT NULL,
    consent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN
        ('ACTIVE','SUBMITTED','UNDER_REVIEW','COMPLETED','WITHDRAWN','EXPIRED')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ,
    UNIQUE (id, workspace_id),
    FOREIGN KEY (commission_id, workspace_id) REFERENCES td_commission(id, workspace_id),
    FOREIGN KEY (commission_id, brief_revision)
        REFERENCES td_brief_revision(commission_id, revision),
    FOREIGN KEY (tester_id, workspace_id) REFERENCES td_tester(id, workspace_id)
);
ALTER TABLE td_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_assignment FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_assignment
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_assignment_tester ON td_assignment (workspace_id, tester_id);

-- INV-02: at most one live assignment per commission. Terminal states (WITHDRAWN, EXPIRED)
-- release the slot so an agency can re-offer; COMPLETED and review states never do.
CREATE UNIQUE INDEX td_assignment_one_live_per_commission ON td_assignment (commission_id)
    WHERE status IN ('ACTIVE','SUBMITTED','UNDER_REVIEW','COMPLETED');

-- Terminal statuses never reopen. ACTIVE may reach any non-initial state; SUBMITTED and
-- UNDER_REVIEW move forward only; WITHDRAWN and EXPIRED are end states. This is the state
-- machine in the schema, so a confused code path gets a database error, not a silent rewrite.
CREATE FUNCTION guard_td_assignment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'assignments are withdrawn or completed, never deleted'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.commission_id <> OLD.commission_id OR NEW.tester_id <> OLD.tester_id
       OR NEW.brief_revision <> OLD.brief_revision OR NEW.id <> OLD.id
       OR NEW.workspace_id <> OLD.workspace_id THEN
        RAISE EXCEPTION 'assignment identity fields are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.status IN ('COMPLETED','WITHDRAWN','EXPIRED') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'a terminal assignment cannot reopen'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER td_assignment_guard BEFORE UPDATE OR DELETE ON td_assignment
    FOR EACH ROW EXECUTE FUNCTION guard_td_assignment();
