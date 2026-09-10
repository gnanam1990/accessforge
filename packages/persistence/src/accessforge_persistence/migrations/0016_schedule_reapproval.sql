-- Module 18 completion: a re-approved schedule records who re-approved it.
--
-- A schedule stores the grant revision it was approved against, and every occurrence is rechecked
-- against it. A restore moves that revision twice -- once when reconciliation marks the grant as
-- needing revalidation, once when a person clears it -- so every schedule stops firing and needs a
-- deliberate re-approval to resume.
--
-- That re-approval is a person deciding "this recurring job should start again under the
-- authorization as it now stands". Recording it without a name would repeat the mistake the grant
-- revalidation columns exist to avoid: "somebody confirmed this" with nobody attached is not a
-- confirmation, and the question after an unexpected run is always who let it resume.
--
-- Deliberately separate from `created_by`. The person who set a schedule up months ago and the
-- person who decided it should resume after an incident are usually not the same, and collapsing
-- them would attribute the second decision to whoever made the first.

ALTER TABLE schedule
    ADD COLUMN IF NOT EXISTS reapproved_at TIMESTAMPTZ;

ALTER TABLE schedule
    ADD COLUMN IF NOT EXISTS reapproved_by UUID REFERENCES app_user (id) ON DELETE SET NULL;

ALTER TABLE schedule
    DROP CONSTRAINT IF EXISTS reapproval_is_attributable;

ALTER TABLE schedule
    ADD CONSTRAINT reapproval_is_attributable
    CHECK ((reapproved_at IS NULL) = (reapproved_by IS NULL));
