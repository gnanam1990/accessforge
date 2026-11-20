-- Tester Desk: the commercial surface that procures an independent human retest of one user
-- journey on one release/browser/assistive-technology combination. Separate from the diagnostic
-- product: a commission pins an immutable brief at publication, and every accepted assignment is
-- bound to the exact brief revision it was claimed under (INV-01).

-- The client of an agency is not a workspace member. Reports are issued to a client identity, and
-- individual people read them through an explicit, revocable viewer grant -- the same grant that
-- makes "a different client identity" a refusal rather than a filtered list (T-012).
CREATE TABLE td_client (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, workspace_id),
    UNIQUE (workspace_id, name)
);
ALTER TABLE td_client ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_client FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_client
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE TABLE td_client_viewer (
    workspace_id UUID NOT NULL,
    client_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES app_user(id),
    granted_by UUID NOT NULL REFERENCES app_user(id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    revoked_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, client_id, user_id),
    FOREIGN KEY (client_id, workspace_id) REFERENCES td_client(id, workspace_id)
);
ALTER TABLE td_client_viewer ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_client_viewer FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_client_viewer
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- Versioned consent terms. A tester reviews one exact version before claiming; the assignment
-- records which version it accepted so a later text change cannot retroactively alter what was
-- agreed. The table is append-only: editing a published version would falsify signed consent.
CREATE TABLE td_consent_terms (
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    version INT NOT NULL CHECK (version >= 1),
    body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 20000),
    created_by UUID NOT NULL REFERENCES app_user(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_id, version)
);
ALTER TABLE td_consent_terms ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_consent_terms FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_consent_terms
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE FUNCTION guard_td_consent_terms() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'consent terms are append-only; publish a new version to change the text'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER td_consent_terms_guard BEFORE UPDATE OR DELETE ON td_consent_terms
    FOR EACH ROW EXECUTE FUNCTION guard_td_consent_terms();

CREATE TABLE td_commission (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    client_id UUID NOT NULL,
    reference TEXT NOT NULL CHECK (char_length(reference) BETWEEN 1 AND 80),
    -- Retest linkage: defect_key groups retests of the same defect; predecessor points at the
    -- earlier commission this one re-tests. Comparison is computed, never inherited (INV-07).
    defect_key TEXT,
    predecessor_commission_id UUID,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN
        ('DRAFT','OPEN','ASSIGNED','SUBMITTED','IN_REVIEW','ACCEPTED','NEEDS_REVISION',
         'EXPIRED','CANCELLED')),
    current_brief_revision INT,
    offer_expires_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES app_user(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, workspace_id),
    UNIQUE (workspace_id, reference),
    FOREIGN KEY (client_id, workspace_id) REFERENCES td_client(id, workspace_id),
    FOREIGN KEY (predecessor_commission_id, workspace_id)
        REFERENCES td_commission(id, workspace_id)
);
ALTER TABLE td_commission ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_commission FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_commission
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
CREATE INDEX td_commission_status ON td_commission (workspace_id, status);
CREATE INDEX td_commission_defect ON td_commission (workspace_id, defect_key)
    WHERE defect_key IS NOT NULL;

-- The pinned commercial terms of one publication. Environment, journey, fee and deliverable
-- rubric are hashed into brief_hash so an assignment can prove which terms it was claimed under.
-- access_note carries the target credentials the assigned tester needs; it is never part of the
-- offer listing or the client report -- only the agency and the claim winner may read it.
CREATE TABLE td_brief_revision (
    commission_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    revision INT NOT NULL CHECK (revision >= 1),
    release_label TEXT NOT NULL CHECK (char_length(release_label) BETWEEN 1 AND 80),
    release_digest TEXT NOT NULL CHECK (release_digest ~ '^[0-9a-f]{64}$'),
    target_url TEXT NOT NULL CHECK (char_length(target_url) BETWEEN 1 AND 2000),
    access_note TEXT,
    task_steps JSONB NOT NULL CHECK (jsonb_typeof(task_steps) = 'array'
        AND octet_length(task_steps::text) <= 32768),
    environment JSONB NOT NULL CHECK (jsonb_typeof(environment) = 'object'),
    rubric JSONB NOT NULL CHECK (jsonb_typeof(rubric) = 'object'),
    permitted_evidence JSONB NOT NULL CHECK (jsonb_typeof(permitted_evidence) = 'array'),
    fee_minor BIGINT NOT NULL CHECK (fee_minor >= 0 AND fee_minor < 9007199254740992),
    currency TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    consent_terms_version INT NOT NULL,
    deadline TIMESTAMPTZ,
    brief_hash TEXT NOT NULL CHECK (brief_hash ~ '^[0-9a-f]{64}$'),
    authored_by UUID NOT NULL REFERENCES app_user(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (commission_id, revision),
    UNIQUE (commission_id, workspace_id, revision),
    FOREIGN KEY (commission_id, workspace_id) REFERENCES td_commission(id, workspace_id),
    FOREIGN KEY (workspace_id, consent_terms_version)
        REFERENCES td_consent_terms(workspace_id, version)
);
ALTER TABLE td_brief_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE td_brief_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON td_brief_revision
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- A brief revision is immutable. A changed fee, step list or rubric is a new revision, and an
-- accepted assignment keeps pointing at the revision it agreed to.
CREATE FUNCTION guard_td_brief_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'a published brief revision is immutable; author a new revision'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE TRIGGER td_brief_revision_guard BEFORE UPDATE OR DELETE ON td_brief_revision
    FOR EACH ROW EXECUTE FUNCTION guard_td_brief_revision();

-- The commissioning identity of a commission does not change: reference, client and author are
-- part of the record an auditor reconciles against the brief revisions.
CREATE FUNCTION guard_td_commission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'a commission is cancelled or expired, never deleted'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.reference <> OLD.reference OR NEW.client_id <> OLD.client_id
       OR NEW.created_by <> OLD.created_by OR NEW.id <> OLD.id
       OR NEW.workspace_id <> OLD.workspace_id THEN
        RAISE EXCEPTION 'commission identity fields are immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER td_commission_guard BEFORE UPDATE OR DELETE ON td_commission
    FOR EACH ROW EXECUTE FUNCTION guard_td_commission();
