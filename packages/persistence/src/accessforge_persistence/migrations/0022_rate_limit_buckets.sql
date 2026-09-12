-- Module 26's rate-limiting gap: a bound on how fast one principal, and one workspace, may write.
--
-- **In the database, not in the process.** An in-memory counter is correct only while there is
-- exactly one API process, which is true on a laptop and false everywhere this is meant to run. Two
-- processes behind a load balancer each enforce their own half of the limit, so the real limit is
-- whatever the deployment happens to be scaled to -- a limit that loosens as you add capacity is
-- not a limit. One row per bucket, updated atomically, is shared by every process.
--
-- **A token bucket, not a fixed window.** A fixed window lets a caller spend the whole allowance at
-- the end of one window and again at the start of the next, so the observed burst is twice the
-- configured rate at every boundary. Tokens refill continuously from the elapsed time, so the
-- allowance is the allowance.

CREATE TABLE IF NOT EXISTS rate_limit_bucket (
    -- PRINCIPAL buckets are global: one human, one allowance, whatever workspace they are acting in.
    -- WORKSPACE buckets are per tenant. The two are independent, which is the point -- one busy
    -- member must not exhaust a workspace, and one busy workspace must not exhaust a member's own
    -- allowance elsewhere.
    scope_kind    TEXT NOT NULL CHECK (scope_kind IN ('PRINCIPAL', 'WORKSPACE')),
    scope_id      UUID NOT NULL,

    -- NULL for a PRINCIPAL bucket, and that is deliberate rather than missing data: a principal's
    -- allowance spans workspaces, so the row cannot belong to one. The row-level security policy
    -- below admits those rows on purpose, and the CHECK makes the pairing exact so a workspace
    -- bucket can never be written as a global one.
    workspace_id  UUID REFERENCES workspace (id) ON DELETE CASCADE,

    -- Fractional on purpose. Integer tokens would round every partial refill down to nothing, so a
    -- caller arriving steadily just under the refill interval would be refused for ever.
    tokens        DOUBLE PRECISION NOT NULL CHECK (tokens >= 0),

    -- When `tokens` was last correct. Refill is computed from the gap between this and now, which is
    -- why nothing here stores a window: there is no window to be on the wrong side of.
    refilled_at   TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (scope_kind, scope_id),

    CONSTRAINT workspace_scope_is_exact CHECK (
        (scope_kind = 'WORKSPACE' AND workspace_id = scope_id)
        OR (scope_kind = 'PRINCIPAL' AND workspace_id IS NULL)
    )
);

-- For the pruning sweep, which looks for buckets nobody has touched.
CREATE INDEX IF NOT EXISTS rate_limit_bucket_idle ON rate_limit_bucket (refilled_at);

COMMENT ON TABLE rate_limit_bucket IS
    'Token buckets bounding write rate per principal and per workspace. Shared across API processes '
    'because a per-process counter is not a limit. Rows hold a counter and an identifier the server '
    'derived from a verified session -- never anything a caller supplied.';

ALTER TABLE rate_limit_bucket ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limit_bucket FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_isolation ON rate_limit_bucket;

-- The one deliberate exception to workspace isolation in this schema, and it needs its reasons on
-- the record.
--
-- A principal's allowance is global: a member of four workspaces has one write rate, not four. A
-- strictly scoped policy would force the principal bucket to be per (user, workspace), which means
-- the limit multiplies by the number of workspaces somebody is a member of -- so the control could
-- be widened by being invited to another tenant.
--
-- What the exception exposes is a counter and a user id, to a connection that can only reach it
-- through this application, which only ever writes the bucket of the principal its own session
-- resolved. No caller chooses a scope_id. A tenant's *data* stays scoped: a WORKSPACE bucket carries
-- its workspace_id and is invisible to every other tenant, which is asserted by a test.
CREATE POLICY workspace_isolation ON rate_limit_bucket
    USING (workspace_id = current_workspace_id() OR workspace_id IS NULL)
    WITH CHECK (workspace_id = current_workspace_id() OR workspace_id IS NULL);
