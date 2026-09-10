-- Module 26: manually configured entitlements, idempotent usage metering, and retention policy.
--
-- Three tables, and the shape of each is a decision about what this system is allowed to claim.
--
-- **An entitlement is configured by a person, and it is revisioned.** There is no checkout, no
-- automatic top-up, no money movement and no currency column anywhere in this file. R1's scope is
-- measured usage and an administrator-managed limit; a schema that could hold a price would grow a
-- billing system, and a billing system that grew out of a metering table is one nobody designed.
--
-- **Usage events are idempotent by construction.** At-least-once delivery is the only kind this
-- system has, so a metering table without a natural key is a table that double-counts under
-- redelivery. The key is the producer's own identifier for the event, and the unique index is what
-- makes the accounting true rather than approximately true.
--
-- **Measurement is distinguished from estimation.** `basis` is NOT NULL with three values, because
-- an operator deciding whether a workspace is near its limit needs to know whether the number is
-- something the system counted, something it inferred, or something it could not obtain. A single
-- integer would make all three look alike, and the third look like zero.

-- ---------------------------------------------------------------------------------------------
-- Entitlements: what an administrator has permitted this workspace to consume
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_entitlement (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- Revisioned rather than updated. An entitlement that changed in place would make every past
    -- admission decision unexplainable: the row would say what is permitted now, and nothing would
    -- say what was permitted when the run was admitted.
    revision      BIGINT NOT NULL CHECK (revision >= 1),

    -- Bounds, all required. There is no NULL meaning "unlimited": an unbounded action count is an
    -- open-ended licence to drive a desktop, and an unbounded model spend is an open-ended licence
    -- to spend somebody's money.
    max_runs_per_day        INTEGER NOT NULL CHECK (max_runs_per_day >= 0),
    max_actions_per_day     INTEGER NOT NULL CHECK (max_actions_per_day >= 0),
    max_wall_seconds_per_day INTEGER NOT NULL CHECK (max_wall_seconds_per_day >= 0),
    max_model_tokens_per_day BIGINT NOT NULL CHECK (max_model_tokens_per_day >= 0),
    max_concurrent_runs     INTEGER NOT NULL CHECK (max_concurrent_runs >= 0),

    -- Who set it, and why. An entitlement with no author is a limit nobody can be asked about.
    configured_by TEXT NOT NULL,
    reason        TEXT NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (workspace_id, revision),
    UNIQUE (id, workspace_id)
);

COMMENT ON TABLE workspace_entitlement IS
    'Administrator-configured limits, appended as revisions. No price, no currency, no payment '
    'instrument: R1 measures usage and enforces a limit somebody set, and collects no money.';

CREATE OR REPLACE FUNCTION refuse_entitlement_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'entitlement revision % is immutable; configuring a new limit appends a revision',
        OLD.revision
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS entitlement_is_immutable ON workspace_entitlement;
CREATE TRIGGER entitlement_is_immutable
    BEFORE UPDATE ON workspace_entitlement
    FOR EACH ROW EXECUTE FUNCTION refuse_entitlement_mutation();

-- ---------------------------------------------------------------------------------------------
-- Usage: what was actually consumed
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_event (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,

    -- The producer's own identity for this event. Delivery repeats; this must not.
    event_key     TEXT NOT NULL,

    kind          TEXT NOT NULL
        CHECK (kind IN ('RUN_ADMITTED', 'ACTION_DISPATCHED', 'WALL_SECONDS', 'MODEL_TOKENS')),

    -- Non-negative. A negative usage event is a correction, and a correction that looks like a
    -- measurement lets a total be talked downwards by whoever produces the events.
    quantity      BIGINT NOT NULL CHECK (quantity >= 0),

    -- MEASURED: this system counted it. ESTIMATED: derived from something that reports its own
    -- usage, which is not a billing input on its own. UNAVAILABLE: the count could not be obtained,
    -- and quantity is 0 without that meaning nothing happened.
    basis         TEXT NOT NULL CHECK (basis IN ('MEASURED', 'ESTIMATED', 'UNAVAILABLE')),

    run_id        UUID,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The whole point of the table. Redelivery of the same event is a no-op rather than a second
    -- charge against a limit.
    UNIQUE (workspace_id, event_key),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE SET NULL
);

COMMENT ON COLUMN usage_event.basis IS
    'MEASURED, ESTIMATED or UNAVAILABLE. A model provider reporting its own token count is '
    'ESTIMATED: it is not the sole trusted input to a limit that costs somebody money.';

CREATE INDEX IF NOT EXISTS usage_event_by_day
    ON usage_event (workspace_id, kind, occurred_at);

-- ---------------------------------------------------------------------------------------------
-- Retention: how long each class of evidence is kept
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retention_policy (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    revision      BIGINT NOT NULL CHECK (revision >= 1),

    -- One row per class, so a change to speech retention cannot silently change source retention.
    evidence_class TEXT NOT NULL
        CHECK (evidence_class IN (
            'SOURCE_SNAPSHOT', 'FIXTURE_REFERENCE', 'READER_SPEECH', 'SCREEN_RECORDING',
            'DIAGNOSTIC', 'MODEL_EXCHANGE', 'REVIEW_RECORD'
        )),

    -- Days. Zero means "not retained beyond the run", which is a policy; there is no NULL meaning
    -- "forever", because forever is a decision somebody has to write down.
    retain_days   INTEGER NOT NULL CHECK (retain_days >= 0),

    -- Whether this class may be collected at all. Consent is a gate, not a retention period: a
    -- class with consent_required and no consent is not collected, however long the period says.
    consent_required BOOLEAN NOT NULL DEFAULT false,

    configured_by TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (workspace_id, revision, evidence_class)
);

COMMENT ON TABLE retention_policy IS
    'Per-class retention, appended as revisions. Deleting evidence under one of these classes '
    'invalidates any completeness claim that depended on it; the deletion path says so rather than '
    'leaving a tidy-looking evidence set behind.';

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['workspace_entitlement', 'usage_event', 'retention_policy'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
