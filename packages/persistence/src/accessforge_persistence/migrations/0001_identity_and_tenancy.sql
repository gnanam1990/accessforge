-- Module 03: identity, workspace membership and database-level tenant isolation.
--
-- Row-level security here is not decoration around a trusted API layer. The threat being defended
-- against is a SQL path that forgets its workspace predicate — a new repository method, a reporting
-- query, a migration script. Middleware cannot help with any of those, so the database enforces the
-- boundary itself.
--
-- FORCE ROW LEVEL SECURITY is used deliberately so the policies apply to the table owner too.
-- Without it, the role that owns the tables bypasses every policy, and the application connects as
-- that owner in local development — which would make the isolation tests pass while proving
-- nothing. See ADR 0004.

CREATE TABLE IF NOT EXISTS app_user (
    id          UUID PRIMARY KEY,
    email       TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at TIMESTAMPTZ
);

COMMENT ON TABLE app_user IS
    'People. Deliberately minimal: no name, no demographic fields, nothing a review or study '
    'would need. SECURITY-PRIVACY section 4 forbids recording unnecessary identity data.';

CREATE TABLE IF NOT EXISTS workspace (
    id         UUID PRIMARY KEY,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_membership (
    workspace_id UUID        NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    user_id      UUID        NOT NULL REFERENCES app_user (id)  ON DELETE CASCADE,
    role         TEXT        NOT NULL CHECK (role IN ('OWNER', 'MAINTAINER', 'REVIEWER', 'VIEWER')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, user_id)
);

COMMENT ON COLUMN workspace_membership.revoked_at IS
    'Membership is revoked rather than deleted, so an audit trail survives. Every authorization '
    'query must treat a non-null value as no membership at all.';

-- One active membership per (workspace, user). The composite primary key above already enforces
-- this; the role is not part of the key, so a user cannot hold two roles in one workspace.

CREATE TABLE IF NOT EXISTS user_session (
    id              UUID PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    token_hash      TEXT        NOT NULL UNIQUE,
    csrf_token_hash TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    rotated_from    UUID REFERENCES user_session (id) ON DELETE SET NULL
);

COMMENT ON TABLE user_session IS
    'Only hashes are stored. A database read must not yield a usable session or CSRF token, so a '
    'backup or a log of this table is not a credential store.';

CREATE INDEX IF NOT EXISTS user_session_user_active
    ON user_session (user_id) WHERE revoked_at IS NULL;

-- Short-lived, single-use runner enrollment. A permanent device secret is deliberately NOT
-- equivalent to an active run authorization: enrollment produces a device record, and run
-- authority is granted separately per run.
CREATE TABLE IF NOT EXISTS enrollment_credential (
    id            UUID PRIMARY KEY,
    workspace_id  UUID        NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    token_hash    TEXT        NOT NULL UNIQUE,
    created_by    UUID        NOT NULL REFERENCES app_user (id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    redeemed_at   TIMESTAMPTZ,
    redeemed_by_device UUID,
    revoked_at    TIMESTAMPTZ
);

COMMENT ON COLUMN enrollment_credential.redeemed_at IS
    'Single use. Redemption is a conditional UPDATE so two concurrent redemptions cannot both '
    'succeed; the database decides, not the application.';

CREATE TABLE IF NOT EXISTS runner_device (
    id           UUID PRIMARY KEY,
    workspace_id UUID        NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    label        TEXT        NOT NULL,
    platform     TEXT        NOT NULL,
    enrolled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Admission and credential validity are revoked independently: a device can be barred from
    -- new work without invalidating the credential, and vice versa.
    admission_revoked_at  TIMESTAMPTZ,
    credential_revoked_at TIMESTAMPTZ
);

-- Environment authorization: who said this environment may be acted on, and which effects are
-- permitted there.
CREATE TABLE IF NOT EXISTS environment_authorization (
    id              UUID PRIMARY KEY,
    workspace_id    UUID        NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    environment     TEXT        NOT NULL,
    authorized_by   UUID        NOT NULL REFERENCES app_user (id),
    permitted_effects TEXT[]    NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    UNIQUE (workspace_id, environment)
);

-- Audit. Privacy-safe by construction: there is no column for a password, token, reader
-- transcript or demographic attribute, so none can be written by accident.
CREATE TABLE IF NOT EXISTS audit_event (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id UUID        REFERENCES workspace (id) ON DELETE SET NULL,
    actor_user   UUID        REFERENCES app_user (id)  ON DELETE SET NULL,
    actor_service TEXT,
    action       TEXT        NOT NULL,
    target_kind  TEXT        NOT NULL,
    target_id    TEXT,
    outcome      TEXT        NOT NULL CHECK (outcome IN ('ALLOWED', 'DENIED')),
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail       JSONB       NOT NULL DEFAULT '{}'
);

COMMENT ON TABLE audit_event IS
    'Records denials as well as allowances. A denial that leaves no trace is indistinguishable '
    'from a request that was never made.';

CREATE INDEX IF NOT EXISTS audit_event_workspace_time
    ON audit_event (workspace_id, occurred_at DESC);

-- ---------------------------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------------------------
-- Every workspace-scoped table is gated on a session variable that the application sets per
-- connection. An empty or unset variable matches nothing, so a query that forgets to establish
-- the workspace sees zero rows rather than everything.

CREATE OR REPLACE FUNCTION current_workspace_id() RETURNS UUID
    LANGUAGE plpgsql STABLE AS $$
DECLARE
    raw TEXT := current_setting('accessforge.workspace_id', true);
BEGIN
    IF raw IS NULL OR raw = '' THEN
        -- NULL compares false against any workspace_id, so no rows are visible. Failing closed is
        -- the whole point: an unset variable must not mean "all workspaces".
        RETURN NULL;
    END IF;
    RETURN raw::UUID;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN NULL;
END;
$$;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'workspace_membership',
        'enrollment_credential',
        'runner_device',
        'environment_authorization'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        -- FORCE so the table owner is subject to the policies too. Without this the owner bypasses
        -- them, and in local development the application connects as the owner.
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;

-- audit_event allows a NULL workspace for workspace-independent actions (sign-in, for example),
-- so it gets its own policy rather than the shared one.
ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON audit_event;
CREATE POLICY workspace_isolation ON audit_event
    USING (workspace_id IS NULL OR workspace_id = current_workspace_id())
    WITH CHECK (workspace_id IS NULL OR workspace_id = current_workspace_id());
