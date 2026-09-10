-- Module 05 correction: a manifest digest over sealed inputs must NOT be unique.
--
-- The UNIQUE constraint looked right and was wrong. CONTRACTS section 4 is explicit that repeated
-- execution creates a new runId and a fresh fixture instance "even when source/journey are
-- unchanged", so two runs legitimately share a set of input digests.
--
-- Worse, forbidding the repeat would forbid the comparison INV-04 depends on: a baseline and its
-- candidate must differ only by the approved patch, and comparing their input digests is exactly how
-- that is demonstrated. A unique constraint here would have made the product's central verification
-- step impossible.
--
-- What must be unique is the binding of a seal to a run: one run has one sealed manifest.

ALTER TABLE sealed_manifest DROP CONSTRAINT IF EXISTS sealed_manifest_manifest_digest_key;

CREATE UNIQUE INDEX IF NOT EXISTS sealed_manifest_one_per_run
    ON sealed_manifest (run_id) WHERE run_id IS NOT NULL;

COMMENT ON COLUMN sealed_manifest.manifest_digest IS
    'Digest over the sealed inputs, deliberately not unique. Two runs with identical inputs share it, '
    'which is how a baseline and candidate are shown to differ only by an approved patch (INV-04).';
