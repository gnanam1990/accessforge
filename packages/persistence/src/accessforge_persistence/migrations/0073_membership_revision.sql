-- Optimistic concurrency for owner membership decisions. No memberships are granted here.
ALTER TABLE workspace_membership ADD COLUMN revision BIGINT NOT NULL DEFAULT 1
    CHECK (revision > 0);
