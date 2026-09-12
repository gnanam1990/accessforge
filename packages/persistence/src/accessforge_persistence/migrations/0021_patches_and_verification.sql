-- FR-010 and FR-011: a proposed repair, and whether it was established.
--
-- Two tables, because they answer two questions that must not be allowed to blur. `patch_proposal`
-- records what somebody proposes to change and under what authority. `patch_verification` records
-- the attempt to establish that it worked. A single table with a status column would make
-- "approved" and "verified" neighbouring values of one field, and the whole point of FR-011 is that
-- reaching the second requires evidence the first does not.
--
-- What is deliberately NOT here: anywhere to store a verdict a human typed. VERIFIED is derived
-- from recorded identities and evidence, never set. CONTRACTS is explicit -- "human review cannot
-- convert an INCONCLUSIVE candidate to VERIFIED" -- and a nullable `outcome` column that a route
-- could write would be the hole that rule exists to close.

-- The repair surface: the only paths a patch for this project may touch.
--
-- Configuration, not request input. It used to arrive in the proposal body as `applicationPaths`,
-- which meant the agent proposing the change also chose how far it was allowed to reach -- and an
-- omitted or empty list disabled confinement entirely, so the laziest possible request got the
-- widest possible surface. A confinement boundary an untrusted caller can widen is decoration.
--
-- No row means no proposals. Refusing is the only safe default: "unconfigured" cannot mean
-- "unrestricted" for the one setting that bounds what an agent may rewrite.
CREATE TABLE IF NOT EXISTS project_repair_surface (
    project_id     UUID PRIMARY KEY,
    workspace_id   UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- Path prefixes, relative to the repository root. Non-empty: a configured surface of nothing is
    -- indistinguishable from no surface, and one of those must refuse proposals.
    paths          TEXT[] NOT NULL CHECK (cardinality(paths) > 0),

    configured_by  UUID NOT NULL REFERENCES app_user (id),
    configured_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision       BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),

    FOREIGN KEY (project_id, workspace_id)
        REFERENCES project (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (project_id, workspace_id)
);

ALTER TABLE project_repair_surface ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_repair_surface FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON project_repair_surface;
CREATE POLICY workspace_isolation ON project_repair_surface
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

COMMENT ON TABLE project_repair_surface IS
    'The paths a repair patch may touch, per project. Trusted configuration: a proposal cannot '
    'supply or widen it, and a project with no row here accepts no patches.';

CREATE TABLE IF NOT EXISTS patch_proposal (
    id                 UUID PRIMARY KEY,
    workspace_id       UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- What this repair is for. A patch with no finding is a change nobody asked for, and the
    -- finding is what the verification has to still be about afterwards.
    finding_id         UUID NOT NULL,

    -- The identity the patch is against, both halves. `base_manifest_digest` is the sealed manifest
    -- the baseline run used; `base_source_digest` is the exact source tree. Recording only the
    -- manifest would let the source move underneath an approval, which is the stale-base case.
    base_manifest_digest TEXT NOT NULL CHECK (base_manifest_digest ~ '^[0-9a-f]{64}$'),
    base_source_digest   TEXT NOT NULL CHECK (base_source_digest ~ '^[0-9a-f]{64}$'),

    -- The digest an approval binds to. Stored rather than recomputed on read: a recomputation
    -- shares any bug in the function that produced the approved value, so it would agree with
    -- itself while disagreeing with what was approved. The changes themselves live in
    -- `patch_change`, and loading a proposal recomputes their digest and refuses a mismatch --
    -- which is the check this column exists to make possible.
    patch_digest       TEXT NOT NULL CHECK (patch_digest ~ '^[0-9a-f]{64}$'),

    status             TEXT NOT NULL CHECK (status IN (
        'PROPOSED', 'APPROVED', 'BUILDING', 'VERIFYING', 'VERIFIED',
        'REVIEW_ACCEPTED', 'REJECTED', 'STALE', 'FAILED'
    )),

    -- The PATCH_APPLY approval this was dispatched under, once it has one. NULL while PROPOSED.
    approval_id        UUID,

    proposed_by        UUID NOT NULL REFERENCES app_user (id),
    rationale          TEXT NOT NULL CHECK (length(btrim(rationale)) > 0),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision           BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),

    FOREIGN KEY (finding_id, workspace_id)
        REFERENCES finding (id, workspace_id) ON DELETE RESTRICT,
    UNIQUE (id, workspace_id)
);

CREATE INDEX IF NOT EXISTS patch_proposal_by_finding
    ON patch_proposal (finding_id, created_at);

-- The diff itself, one row per path. Without this a proposal was a list of filenames and a digest:
-- nothing could reload what was actually proposed, a reviewer could not read the change they were
-- approving, and the bytes an approval bound to existed only in the request that created it.
--
-- Content in the database rather than the object store, deliberately and with limits. A repair diff
-- is small -- the whole point of FR-010 is a bounded, reviewable change -- and a proposal whose
-- bytes live somewhere with its own retention and failure modes is a proposal that can lose the
-- thing an approval was granted for. The caps are enforced in `patches.propose_patch` so the error
-- names the path and the limit.
CREATE TABLE IF NOT EXISTS patch_change (
    id             UUID PRIMARY KEY,
    workspace_id   UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    patch_id       UUID NOT NULL,

    -- Ordering is the proposal's own, preserved so a reloaded patch is byte-identical to the one
    -- whose digest was approved. The digest sorts by path, but a reviewer reads the diff in the
    -- order it was written.
    ordinal        INTEGER NOT NULL CHECK (ordinal >= 0),

    path           TEXT NOT NULL CHECK (length(btrim(path)) > 0),

    -- MODIFY carries content; DELETE carries none. Stored as an operation rather than inferred from
    -- a NULL, because "content is null" would otherwise mean both "this file is removed" and
    -- "nobody recorded what this change was".
    operation      TEXT NOT NULL CHECK (operation IN ('MODIFY', 'DELETE')),
    content        TEXT,

    -- The git file mode when the proposer knew it. This is how a symlink is recognised: the path and
    -- diff of a symlink addition look like an ordinary small text file (INV-16).
    mode           TEXT,

    -- `is_binary`, not `binary`: the latter is a reserved word in PostgreSQL, so the column would
    -- have needed quoting at every use and the first unquoted one would have been a syntax error.
    is_binary      BOOLEAN NOT NULL DEFAULT false,

    -- What the policy ruled, kept with the change rather than summarised on the proposal. A
    -- reviewer needs to see which specific path was allowed only as separately reviewed scope.
    verdict        TEXT NOT NULL CHECK (verdict IN ('ALLOWED', 'SEPARATELY_REVIEWED')),

    FOREIGN KEY (patch_id, workspace_id)
        REFERENCES patch_proposal (id, workspace_id) ON DELETE CASCADE,

    -- One row per path per patch, and one ordinal per patch. A repeated path would make the
    -- reloaded patch ambiguous, and its digest unreproducible.
    UNIQUE (patch_id, path),
    UNIQUE (patch_id, ordinal),

    -- A DELETE with content is a contradiction; a MODIFY without content records no change. Either
    -- would survive into a candidate workspace as something nobody proposed.
    CONSTRAINT content_matches_operation CHECK (
        (operation = 'DELETE' AND content IS NULL)
        OR (operation = 'MODIFY' AND content IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS patch_change_by_patch ON patch_change (patch_id, ordinal);

COMMENT ON TABLE patch_change IS
    'The exact bytes a patch proposes, per path. An approval binds to the digest of these rows, so '
    'they are the record of what was approved -- not a summary of it.';

-- Every status move, with who and why. A patch that went STALE or FAILED and came back needs to
-- say so: the history is what distinguishes "approved once" from "approved, invalidated by a base
-- change, and approved again against the new base".
CREATE TABLE IF NOT EXISTS patch_proposal_transition (
    id             BIGSERIAL PRIMARY KEY,
    workspace_id   UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    patch_id       UUID NOT NULL,
    from_status    TEXT,
    to_status      TEXT NOT NULL,
    actor_id       UUID,
    reason         TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    FOREIGN KEY (patch_id, workspace_id)
        REFERENCES patch_proposal (id, workspace_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS patch_proposal_transition_by_patch
    ON patch_proposal_transition (patch_id, id);

-- FR-011. One row per verification attempt, linking the complete failed baseline to the candidate.
CREATE TABLE IF NOT EXISTS patch_verification (
    id                 UUID PRIMARY KEY,
    workspace_id       UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    patch_id           UUID NOT NULL,

    -- The baseline: the run that established the failure. Required at creation, because a
    -- verification with nothing to compare against is a candidate run with opinions.
    baseline_run_id    UUID NOT NULL,

    -- The candidate run, once one exists. NULL while BUILDING.
    candidate_run_id   UUID,

    -- Identities compared before dispatch. Stored as observed rather than as a boolean verdict, so
    -- a reader can see *what* differed rather than being told that something did.
    baseline_identity  JSONB NOT NULL,
    candidate_identity JSONB,

    -- Differences explicitly recorded as legitimate (INV-04). Anything not named here and not the
    -- approved patch is unexplained drift, which disqualifies the pair.
    permitted_differences JSONB NOT NULL DEFAULT '[]'::jsonb,

    state              TEXT NOT NULL CHECK (state IN ('BUILDING', 'VERIFYING', 'CONCLUDED')),

    -- The conclusion, once concluded. Never 'VERIFIED' unless the gates passed: the CHECK below is
    -- the schema's half of that, and the gates themselves are in the persistence layer.
    conclusion         TEXT CHECK (conclusion IN ('VERIFIED', 'NOT_ESTABLISHED', 'INCONCLUSIVE')),

    -- Why, in words, always. A conclusion nobody can explain is the one an auditor asks about.
    conclusion_reasons TEXT[] NOT NULL DEFAULT '{}',

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    concluded_at       TIMESTAMPTZ,
    revision           BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),

    FOREIGN KEY (patch_id, workspace_id)
        REFERENCES patch_proposal (id, workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY (baseline_run_id, workspace_id)
        REFERENCES run (id, workspace_id) ON DELETE RESTRICT,
    FOREIGN KEY (candidate_run_id, workspace_id)
        REFERENCES run (id, workspace_id) ON DELETE RESTRICT,

    -- A conclusion and a concluded state arrive together or not at all. Half of either is a
    -- verification that reads as finished while saying nothing, or as open while holding a verdict.
    CONSTRAINT conclusion_is_complete CHECK (
        (state = 'CONCLUDED') = (conclusion IS NOT NULL AND concluded_at IS NOT NULL)
    ),

    -- A conclusion must carry its reasons. Especially VERIFIED: the reasons are the record of which
    -- gates were actually checked, and a VERIFIED with none would be unauditable.
    CONSTRAINT conclusion_is_explained CHECK (
        conclusion IS NULL OR cardinality(conclusion_reasons) > 0
    ),

    -- VERIFIED requires a candidate. Enforced here as well as in code because this is the one
    -- transition the whole module exists to make hard to reach by accident.
    CONSTRAINT verified_requires_a_candidate CHECK (
        conclusion IS DISTINCT FROM 'VERIFIED' OR candidate_run_id IS NOT NULL
    ),

    UNIQUE (id, workspace_id)
);

CREATE INDEX IF NOT EXISTS patch_verification_by_patch
    ON patch_verification (patch_id, created_at);

COMMENT ON TABLE patch_verification IS
    'One attempt to establish that a patch fixed the reproduced behaviour. NOT_ESTABLISHED means '
    'the repair was not shown to work; the original finding and all its evidence survive it.';

-- Row-level security on all three. A patch proposal names source paths in a customer repository and
-- a rationale written about their defect; a verification names the runs it compared.
-- FORCE, not merely ENABLE: the application role owns these tables, and ENABLE does nothing for an
-- owner.
ALTER TABLE patch_proposal ENABLE ROW LEVEL SECURITY;
ALTER TABLE patch_proposal FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON patch_proposal;
CREATE POLICY workspace_isolation ON patch_proposal
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE patch_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE patch_change FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON patch_change;
CREATE POLICY workspace_isolation ON patch_change
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE patch_proposal_transition ENABLE ROW LEVEL SECURITY;
ALTER TABLE patch_proposal_transition FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON patch_proposal_transition;
CREATE POLICY workspace_isolation ON patch_proposal_transition
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE patch_verification ENABLE ROW LEVEL SECURITY;
ALTER TABLE patch_verification FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON patch_verification;
CREATE POLICY workspace_isolation ON patch_verification
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
