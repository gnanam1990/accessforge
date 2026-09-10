-- Module 04: the authoritative journal, durable jobs and the transactional outbox.
--
-- The shape of this schema is driven by one question: after a process dies at an arbitrary instant,
-- what can be proved about what happened? Three rules follow from it.
--
--   * State and its outbox message commit together. A queue message that exists before the state it
--     refers to would let a consumer act on work that was never accepted.
--   * Delivery is at-least-once. Nothing here assumes a queue delivers exactly once, because no
--     queue does; consumers re-read state and deduplicate on a stable operation identity.
--   * Terminal records are immutable, enforced by a trigger rather than by convention. Corrections
--     are separate append-only rows (INV-11).
--
-- Everything workspace-scoped carries the same FORCE row-level security as module 03.

-- ---------------------------------------------------------------------------------------------
-- Operations and idempotency
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operation (
    id              UUID PRIMARY KEY,
    workspace_id    UUID        NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    principal_id    TEXT        NOT NULL,
    route           TEXT        NOT NULL,
    idempotency_key TEXT        NOT NULL,
    request_digest  TEXT        NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    status          TEXT        NOT NULL CHECK (status IN ('ACCEPTED', 'COMPLETED', 'FAILED')),
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL,
    -- The idempotency identity. Binding the principal and route as well as the key means one
    -- caller's key cannot collide with, or replay, another's.
    UNIQUE (workspace_id, principal_id, route, idempotency_key)
);

COMMENT ON COLUMN operation.request_digest IS
    'RFC8785 canonical digest of the request body. Same key with the same body replays the accepted '
    'operation; same key with a different body is a conflict, not an accidental overwrite.';

COMMENT ON COLUMN operation.expires_at IS
    'Idempotency records are retained for a bounded window, not forever. Past it the key may be '
    'reused, which is a deliberate trade recorded in the handoff.';

-- ---------------------------------------------------------------------------------------------
-- Runs and attempts
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    id                     UUID PRIMARY KEY,
    workspace_id           UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id             UUID,
    manifest_digest        TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    authorization_id       UUID,

    -- Mirrors accessforge_domain.reducers.RunState. The reducers remain the only authority on
    -- which transitions are admissible; this table records their results.
    status                 TEXT NOT NULL CHECK (status IN
                               ('QUEUED','LEASED','RUNNING','FINALIZING','COMPLETED',
                                'INTERRUPTED','CANCELLED')),
    outcome                TEXT NOT NULL CHECK (outcome IN
                               ('NOT_EVALUATED','PASS','FAIL','INCONCLUSIVE')),
    revision               BIGINT      NOT NULL DEFAULT 0 CHECK (revision >= 0),
    lease_epoch            BIGINT      NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
    execution_began        BOOLEAN     NOT NULL DEFAULT false,
    unresolved_action      BOOLEAN     NOT NULL DEFAULT false,

    cancel_requested_at    TIMESTAMPTZ,
    cancellation_revision  BIGINT,
    stop_acknowledged_at   TIMESTAMPTZ,
    stop_acknowledged_epoch BIGINT,
    ambiguity_reason       TEXT,
    quarantined            BOOLEAN     NOT NULL DEFAULT false,

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A retry is a new run linked to its predecessor, never a resumed terminal record.
    retry_of               UUID REFERENCES run (id) ON DELETE SET NULL,

    -- The admissible status/outcome pairs from CONTRACTS section 5, enforced here as well as in the
    -- reducers. Two independent guards, because this is the invariant the product is built on.
    CONSTRAINT run_admissible_status_outcome CHECK (
        (status IN ('QUEUED','LEASED','RUNNING','FINALIZING') AND outcome = 'NOT_EVALUATED')
        OR (status = 'COMPLETED'   AND outcome IN ('PASS','FAIL','INCONCLUSIVE'))
        OR (status = 'INTERRUPTED' AND outcome = 'INCONCLUSIVE')
        OR (status = 'CANCELLED'   AND outcome IN ('NOT_EVALUATED','INCONCLUSIVE'))
    ),
    -- Cancellation is two-step: the request is recorded with the revision it was made at.
    CONSTRAINT run_cancellation_pair CHECK (
        (cancel_requested_at IS NULL) = (cancellation_revision IS NULL)
    ),
    CONSTRAINT run_stop_ack_pair CHECK (
        (stop_acknowledged_at IS NULL) = (stop_acknowledged_epoch IS NULL)
    ),
    -- An interrupted run must say why it was ambiguous.
    CONSTRAINT run_interrupted_has_reason CHECK (
        status <> 'INTERRUPTED' OR ambiguity_reason IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS run_workspace_status ON run (workspace_id, status);

CREATE TABLE IF NOT EXISTS run_attempt (
    id           UUID PRIMARY KEY,
    workspace_id UUID   NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id       UUID   NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    lease_epoch  BIGINT NOT NULL CHECK (lease_epoch >= 0),
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    UNIQUE (run_id, lease_epoch)
);

-- ---------------------------------------------------------------------------------------------
-- Immutability of terminal records (INV-11)
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION refuse_terminal_run_mutation() RETURNS TRIGGER
    LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('COMPLETED', 'INTERRUPTED', 'CANCELLED') THEN
        RAISE EXCEPTION
            'run % is terminal in % and is immutable; a retry creates a new linked run',
            OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION refuse_terminal_run_mutation() IS
    'Enforced in the database, not only in the reducers. A terminal record that could be edited by '
    'any future repository method, migration or support query would make every outcome provisional.';

DROP TRIGGER IF EXISTS run_terminal_is_immutable ON run;
CREATE TRIGGER run_terminal_is_immutable
    BEFORE UPDATE OR DELETE ON run
    FOR EACH ROW EXECUTE FUNCTION refuse_terminal_run_mutation();

-- ---------------------------------------------------------------------------------------------
-- Approvals, grants and exact children
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval (
    id                UUID PRIMARY KEY,
    workspace_id      UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    scope             TEXT NOT NULL CHECK (scope IN ('RUN_EFFECTS','PATCH_APPLY','GITHUB_PUBLISH')),
    actor_user        UUID NOT NULL REFERENCES app_user (id),
    target_id         UUID NOT NULL,
    target_digest     TEXT NOT NULL CHECK (target_digest ~ '^[0-9a-f]{64}$'),
    expected_revision BIGINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    revoked_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS execution_grant (
    id                   UUID PRIMARY KEY,
    workspace_id         UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    project_id           UUID NOT NULL,
    environment          TEXT NOT NULL,
    allowed_journey_versions TEXT[] NOT NULL CHECK (cardinality(allowed_journey_versions) > 0),
    allowed_policy_versions  TEXT[] NOT NULL CHECK (cardinality(allowed_policy_versions) > 0),
    permitted_effects    TEXT[] NOT NULL DEFAULT '{}',
    action_budget        INTEGER NOT NULL CHECK (action_budget > 0),
    wall_time_budget_seconds INTEGER NOT NULL CHECK (wall_time_budget_seconds > 0),
    revision             BIGINT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ NOT NULL,
    revoked_at           TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS child_authorization (
    id                    UUID PRIMARY KEY,
    workspace_id          UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id                UUID NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    parent_grant_id       UUID NOT NULL REFERENCES execution_grant (id) ON DELETE CASCADE,
    -- Recorded so dispatch can verify the parent has not moved since minting.
    parent_grant_revision BIGINT NOT NULL,
    issuing_service_identity TEXT NOT NULL CHECK (length(btrim(issuing_service_identity)) > 0),
    target_digest         TEXT NOT NULL CHECK (target_digest ~ '^[0-9a-f]{64}$'),
    permitted_effects     TEXT[] NOT NULL DEFAULT '{}',
    action_budget         INTEGER NOT NULL CHECK (action_budget > 0),
    wall_time_budget_seconds INTEGER NOT NULL CHECK (wall_time_budget_seconds > 0),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at            TIMESTAMPTZ NOT NULL,
    -- One exact child per run: a second would mean two authorizations for the same work.
    UNIQUE (run_id)
);

COMMENT ON COLUMN child_authorization.target_digest IS
    'Immutable. The child binds the exact resolved inputs; changed inputs require a new child '
    'rather than an amended one.';

-- ---------------------------------------------------------------------------------------------
-- Evidence: producer streams, watermarks and the canonical chain
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS producer_stream (
    workspace_id     UUID   NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id           UUID   NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    attempt_id       UUID   NOT NULL REFERENCES run_attempt (id) ON DELETE CASCADE,
    producer_id      TEXT   NOT NULL,
    -- How far this producer's own contiguous sequence has been admitted.
    admitted_through BIGINT NOT NULL DEFAULT 0 CHECK (admitted_through >= 0),
    -- Set only by an authenticated closing watermark. Finalization requires one per required
    -- producer: a contiguous canonical chain does not prove a producer finished (INV-06).
    closed_at_sequence BIGINT,
    closed_at        TIMESTAMPTZ,
    PRIMARY KEY (run_id, attempt_id, producer_id)
);

CREATE TABLE IF NOT EXISTS producer_source_record (
    workspace_id     UUID   NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id           UUID   NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    attempt_id       UUID   NOT NULL REFERENCES run_attempt (id) ON DELETE CASCADE,
    producer_id      TEXT   NOT NULL,
    source_record_id TEXT   NOT NULL,
    source_record_digest TEXT NOT NULL CHECK (source_record_digest ~ '^[0-9a-f]{64}$'),
    producer_sequence BIGINT NOT NULL CHECK (producer_sequence >= 1),
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Replay is keyed by producer plus source record, independently of canonical sequence.
    PRIMARY KEY (run_id, attempt_id, producer_id, source_record_id),
    -- A producer cannot reuse its own sequence number for a different record.
    UNIQUE (run_id, attempt_id, producer_id, producer_sequence)
);

CREATE TABLE IF NOT EXISTS canonical_event (
    workspace_id       UUID   NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id             UUID   NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    attempt_id         UUID   NOT NULL REFERENCES run_attempt (id) ON DELETE CASCADE,
    sequence           BIGINT NOT NULL CHECK (sequence >= 1),
    event_id           UUID   NOT NULL UNIQUE,
    event_type         TEXT   NOT NULL,
    lease_epoch        BIGINT NOT NULL,
    source_time        TIMESTAMPTZ NOT NULL,
    received_time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    manifest_digest    TEXT   NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    previous_event_hash TEXT  NOT NULL CHECK (previous_event_hash ~ '^[0-9a-f]{64}$'),
    payload_digest     TEXT   NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    payload            JSONB  NOT NULL,
    -- Prevents forks: one record per position per attempt.
    PRIMARY KEY (run_id, attempt_id, sequence)
);

COMMENT ON TABLE canonical_event IS
    'Ordered by the integer sequence the trusted sequencer assigns, never by wall-clock time. '
    'Producers submit records; they do not choose their canonical position.';

-- ---------------------------------------------------------------------------------------------
-- Durable jobs and the transactional outbox
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outbox_message (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    -- A stable identity consumers deduplicate against. Delivery may repeat; this must not.
    operation_id  UUID NOT NULL,
    topic         TEXT NOT NULL,
    -- A reference, never a trusted mutable command. The consumer re-reads authoritative state.
    reference     JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at    TIMESTAMPTZ,
    claimed_by    TEXT,
    claim_expires_at TIMESTAMPTZ,
    published_at  TIMESTAMPTZ,
    attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS outbox_unpublished
    ON outbox_message (created_at) WHERE published_at IS NULL;

COMMENT ON TABLE outbox_message IS
    'Written in the same transaction as the state change it announces. Publishing before the state '
    'exists would let a consumer act on work that was never accepted.';

CREATE TABLE IF NOT EXISTS job (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    operation_id  UUID NOT NULL,
    kind          TEXT NOT NULL,
    reference     JSONB NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                      ('PENDING','CLAIMED','SUCCEEDED','FAILED','ABANDONED')),
    attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claimed_by    TEXT,
    claim_expires_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error    TEXT
);

CREATE INDEX IF NOT EXISTS job_claimable ON job (created_at) WHERE status = 'PENDING';

-- ---------------------------------------------------------------------------------------------
-- Row-level security for everything above
-- ---------------------------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'operation', 'run', 'run_attempt', 'approval', 'execution_grant',
        'child_authorization', 'producer_stream', 'producer_source_record',
        'canonical_event', 'outbox_message', 'job'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
