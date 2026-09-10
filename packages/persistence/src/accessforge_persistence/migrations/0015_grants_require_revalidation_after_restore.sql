-- Module 27: a restored execution grant is unusable until a person revalidates it.
--
-- Reconciliation previously *listed* the grants a restore brought back and recorded the count. That
-- is a report, not a control: the grants stayed usable, and a grant revoked an hour after the
-- snapshot is live in the backup and revoked in the world. Nothing in the restored data can tell
-- the difference, so the only correct answer is to fail closed -- and failing closed has to be a
-- column somebody has to clear, not a sentence in a runbook.
--
-- Default false so that every existing grant keeps working: this flag means "a restore brought you
-- back and nobody has confirmed you are still authorized", which is not true of a grant that has
-- been sitting in a live database all along. Reconciliation sets it; a person clears it.
--
-- Deliberately not a `revoked_at`. Revocation is a decision somebody made about the grant.
-- This is the absence of a decision, and collapsing the two would lose the difference between
-- "we withdrew this" and "we cannot currently vouch for it".

ALTER TABLE execution_grant
    ADD COLUMN IF NOT EXISTS revalidation_required BOOLEAN NOT NULL DEFAULT false;

-- Who cleared it and when, so that "somebody revalidated this" is a fact with a name on it rather
-- than a flag that went from true to false at an unknown moment.
ALTER TABLE execution_grant
    ADD COLUMN IF NOT EXISTS revalidated_at TIMESTAMPTZ;

ALTER TABLE execution_grant
    ADD COLUMN IF NOT EXISTS revalidated_by UUID REFERENCES app_user (id) ON DELETE SET NULL;

ALTER TABLE execution_grant
    DROP CONSTRAINT IF EXISTS revalidation_is_attributable;

ALTER TABLE execution_grant
    ADD CONSTRAINT revalidation_is_attributable
    CHECK ((revalidated_at IS NULL) = (revalidated_by IS NULL));
