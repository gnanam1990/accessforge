-- Module 03, second migration: close two isolation gaps found in independent review.
--
-- 1. `audit_event` had a NULL-workspace carve-out:
--
--        USING (workspace_id IS NULL OR workspace_id = current_workspace_id())
--
--    `workspace_id IS NULL` is TRUE under every session's policy evaluation, so workspace-independent
--    rows — a sign-in, carrying an actor id and whatever the caller put in `detail` — were readable
--    by every tenant. Verified: two separate workspaces both read the same sign-in row, including
--    its user id and originating address.
--
--    Workspace-independent events now live in their own table whose policy is the *inverse*: visible
--    only when no workspace scope is established. A tenant connection sees nothing there, ever.
--
-- 2. There was no way for a signed-in user to discover which workspaces they belong to.
--    `workspace_membership` is workspace-scoped, so an unscoped connection saw zero rows and a
--    post-login workspace picker was impossible. Rather than add an RLS bypass, the policy is
--    widened by exactly one predicate: a caller may also see membership rows that are *their own*.

-- ---------------------------------------------------------------------------------------------
-- A second session variable: who is asking, independent of where.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION current_app_user_id() RETURNS UUID
    LANGUAGE plpgsql STABLE AS $$
DECLARE
    raw TEXT := current_setting('accessforge.user_id', true);
BEGIN
    IF raw IS NULL OR raw = '' THEN
        RETURN NULL;
    END IF;
    RETURN raw::UUID;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN NULL;
END;
$$;

-- Widen workspace_membership by one predicate. A caller sees rows for the workspace they are scoped
-- to, plus rows that are their own — never another user's row in a workspace they are not in.
DROP POLICY IF EXISTS workspace_isolation ON workspace_membership;
CREATE POLICY workspace_isolation ON workspace_membership
    USING (
        workspace_id = current_workspace_id()
        OR (current_app_user_id() IS NOT NULL AND user_id = current_app_user_id())
    )
    WITH CHECK (workspace_id = current_workspace_id());

COMMENT ON FUNCTION current_app_user_id() IS
    'The acting user, used only to let someone enumerate their own memberships. Deliberately not '
    'part of any other table policy: identifying a user must not become a general-purpose bypass.';

-- Note the asymmetry above: WITH CHECK still requires a workspace scope. Reading your own
-- memberships is safe; *writing* one must go through the workspace that grants it.

-- ---------------------------------------------------------------------------------------------
-- Split workspace-independent audit events out of the tenant-scoped table.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS global_audit_event (
    id            BIGSERIAL PRIMARY KEY,
    actor_user    UUID REFERENCES app_user (id) ON DELETE SET NULL,
    actor_service TEXT,
    action        TEXT        NOT NULL,
    target_kind   TEXT        NOT NULL,
    target_id     TEXT,
    outcome       TEXT        NOT NULL CHECK (outcome IN ('ALLOWED', 'DENIED')),
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail        JSONB       NOT NULL DEFAULT '{}'
);

COMMENT ON TABLE global_audit_event IS
    'Events that belong to no workspace: sign-in, sign-out, account disable. Readable only without a '
    'workspace scope, which means an operator path — never a tenant connection. Like audit_event it '
    'has no column for a password, token or transcript.';

ALTER TABLE global_audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE global_audit_event FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS operator_only ON global_audit_event;
-- The inverse of the bug: visible only when NO workspace scope is set.
CREATE POLICY operator_only ON global_audit_event
    USING (current_workspace_id() IS NULL)
    WITH CHECK (current_workspace_id() IS NULL);

-- Move any existing workspace-independent rows across, then forbid them in the scoped table.
INSERT INTO global_audit_event
    (actor_user, actor_service, action, target_kind, target_id, outcome, occurred_at, detail)
SELECT actor_user, actor_service, action, target_kind, target_id, outcome, occurred_at, detail
FROM audit_event
WHERE workspace_id IS NULL;

DELETE FROM audit_event WHERE workspace_id IS NULL;

ALTER TABLE audit_event ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE audit_event DROP CONSTRAINT IF EXISTS audit_event_workspace_id_fkey;
ALTER TABLE audit_event
    ADD CONSTRAINT audit_event_workspace_id_fkey
    FOREIGN KEY (workspace_id) REFERENCES workspace (id) ON DELETE CASCADE;

-- Now that NULL is impossible, the policy is an exact match like every other scoped table.
DROP POLICY IF EXISTS workspace_isolation ON audit_event;
CREATE POLICY workspace_isolation ON audit_event
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
