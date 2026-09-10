-- Module 16: human review, and the finding lifecycle it gates.
--
-- A review is a person's assessment of a particular repair. It is not a verdict about the run, and
-- the schema is arranged so it cannot become one: there is no column here through which a review
-- could touch `run.outcome`, and INV-12 requires human opinion and machine outcome to stay
-- separately attributable in every view built on top of these tables.
--
-- The other thing this migration deliberately does not have is a place to put demographic or
-- disability information. Not an unused nullable column, not a JSONB blob that could hold one --
-- nothing. Normal product use requires no such disclosure, and a column that exists gets filled.

CREATE TABLE IF NOT EXISTS review_request (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- What is being reviewed, bound by digest rather than by reference. A patch whose bytes changed
    -- is a different patch, and a review that followed the reference would silently carry its
    -- acceptance forward onto content nobody looked at.
    patch_digest       TEXT NOT NULL CHECK (patch_digest ~ '^[0-9a-f]{64}$'),
    verification_digest TEXT NOT NULL CHECK (verification_digest ~ '^[0-9a-f]{64}$'),
    journey_version_id UUID NOT NULL,
    environment_digest TEXT NOT NULL CHECK (environment_digest ~ '^[0-9a-f]{64}$'),

    requested_by  UUID NOT NULL,
    requested_of  UUID,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    FOREIGN KEY (journey_version_id, workspace_id)
        REFERENCES journey_version (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

COMMENT ON TABLE review_request IS
    'Asking someone to review is an event; their assessment is a different event. Assignment alone '
    'never establishes participation, so a request with no submission is a request nobody answered '
    'rather than a review nobody wrote down.';

COMMENT ON COLUMN review_request.requested_of IS
    'Nullable: an open request that anyone with the role may answer. A named assignee is a '
    'preference, not a lock -- and never evidence that the named person did anything.';

CREATE TABLE IF NOT EXISTS review (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    request_id    UUID,

    -- The canonical actor identity, not a display name and not a session. The independence policy
    -- is enforced on this column, so an alias or a second session must resolve to the same value or
    -- the policy is decoration.
    reviewer_id   UUID NOT NULL,
    reviewer_role TEXT NOT NULL CHECK (reviewer_role IN ('OWNER', 'MAINTAINER', 'REVIEWER')),

    -- Re-bound on the submission itself rather than read through request_id. A request can be
    -- created against one patch and submitted after the patch moved; binding here is what makes
    -- the stale-digest check possible at all.
    patch_digest        TEXT NOT NULL CHECK (patch_digest ~ '^[0-9a-f]{64}$'),
    verification_digest TEXT NOT NULL CHECK (verification_digest ~ '^[0-9a-f]{64}$'),
    journey_version_id  UUID NOT NULL,
    environment_digest  TEXT NOT NULL CHECK (environment_digest ~ '^[0-9a-f]{64}$'),

    verdict       TEXT NOT NULL
        CHECK (verdict IN ('ACCEPT', 'CHANGES_REQUESTED', 'UNABLE_TO_ASSESS')),

    -- What the reviewer saw and what they could not. `limitations` is NOT NULL and may be empty
    -- text, which is a deliberate distinction: an empty string is a reviewer who considered the
    -- question, and NULL would be a reviewer who was never asked.
    observations  TEXT NOT NULL,
    limitations   TEXT NOT NULL,

    -- Whether this person actually used assistive technology while reviewing. Recorded because the
    -- release proof asks, and because "a person accepted this" and "a person accepted this having
    -- driven it with a screen reader" are very different claims. Never inferred.
    used_assistive_technology BOOLEAN NOT NULL,
    assistive_technology_detail TEXT,

    -- Append-only correction. A superseding review points at the one it replaces; neither row is
    -- ever updated, so the history reads as what was said and then what was said instead.
    supersedes    UUID,

    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT at_detail_requires_at CHECK (
        used_assistive_technology OR assistive_technology_detail IS NULL
    ),
    -- UNABLE_TO_ASSESS without saying what was missing is the least useful record in the system:
    -- it reports a person's time was spent and nothing about why they could not answer.
    CONSTRAINT unable_to_assess_states_why CHECK (
        verdict <> 'UNABLE_TO_ASSESS' OR length(trim(limitations)) > 0
    ),
    FOREIGN KEY (request_id, workspace_id) REFERENCES review_request (id, workspace_id),
    FOREIGN KEY (supersedes, workspace_id) REFERENCES review (id, workspace_id),
    FOREIGN KEY (journey_version_id, workspace_id)
        REFERENCES journey_version (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

COMMENT ON COLUMN review.used_assistive_technology IS
    'Never inferred from role, workspace membership or the fact that a review exists. A reviewer '
    'who read a transcript and a reviewer who drove the page with VoiceOver made different claims.';

-- Reviews are immutable. A correction is a new row pointing at the old one.
CREATE OR REPLACE FUNCTION refuse_review_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'review % is immutable; a correction is a new review that supersedes it', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS review_is_immutable ON review;
CREATE TRIGGER review_is_immutable
    BEFORE UPDATE OR DELETE ON review
    FOR EACH ROW EXECUTE FUNCTION refuse_review_mutation();

CREATE INDEX IF NOT EXISTS review_by_patch ON review (workspace_id, patch_digest, submitted_at);

CREATE TABLE IF NOT EXISTS finding (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    assertion_id  TEXT NOT NULL,
    status        TEXT NOT NULL
        CHECK (status IN ('CANDIDATE', 'REPRODUCED', 'DISMISSED', 'RESOLVED')),
    summary       TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision      BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

-- Every status change, append-only, with who and why. A finding's current status is a projection of
-- this table rather than the other side of the story: an auditor asking "who dismissed this and on
-- what grounds" gets an answer, and a status that changed with no row here is impossible.
CREATE TABLE IF NOT EXISTS finding_transition (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    finding_id    UUID NOT NULL,
    from_status   TEXT,
    to_status     TEXT NOT NULL,
    actor_id      UUID NOT NULL,
    reason        TEXT NOT NULL,
    -- The review that authorized this transition, where one is required. RESOLVED without it is
    -- refused in application code and this column is how an auditor confirms which review it was.
    review_id     UUID,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (finding_id, workspace_id) REFERENCES finding (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (review_id, workspace_id) REFERENCES review (id, workspace_id),
    CONSTRAINT transition_has_a_reason CHECK (length(trim(reason)) > 0)
);

CREATE INDEX IF NOT EXISTS finding_transition_by_finding
    ON finding_transition (finding_id, occurred_at);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['review_request', 'review', 'finding', 'finding_transition'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
