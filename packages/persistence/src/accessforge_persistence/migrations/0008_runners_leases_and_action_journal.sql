-- Module 07: runner registry, desktop leases and the action journal.
--
-- The invariant this schema exists to enforce is INV-10: "One physical desktop session has at most
-- one admitted active attempt." It is enforced by a partial unique index on the *physical session
-- key*, not on the runner row and not on anything a supervisor process picks for itself. Two
-- supervisors started on the same signed-in desktop present the same session key, so the second
-- INSERT fails. A process id, container id or hostname would all differ between those two
-- supervisors and admit both to the same screen.
--
-- Leases carry a monotonically increasing epoch per runner. The epoch is how a stale supervisor is
-- recognised after a partition: it holds epoch N, the runner is on N+1, and every submission it
-- makes is refused by identity rather than by timing.

CREATE TABLE IF NOT EXISTS runner (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL
        CHECK (status IN ('OFFLINE', 'PREFLIGHT_REQUIRED', 'READY', 'BUSY', 'QUARANTINED')),

    -- Physical desktop identity. device_id + platform + interactive session id + console, digested.
    -- This is the exclusivity key, and it is NOT NULL because a runner whose desktop we cannot name
    -- cannot be fenced.
    session_key   TEXT NOT NULL CHECK (session_key ~ '^[0-9a-f]{64}$'),
    platform      TEXT NOT NULL CHECK (platform IN ('darwin', 'win32')),
    device_id     TEXT NOT NULL,
    interactive_session_id TEXT NOT NULL,
    console       BOOLEAN NOT NULL,

    profile_digest TEXT NOT NULL CHECK (profile_digest ~ '^[0-9a-f]{64}$'),
    profile        JSONB NOT NULL,

    -- The highest lease epoch ever issued for this runner. Monotonic: it is never reset, not by
    -- reset, not by re-enrollment, not by quarantine release. A reused epoch would make a stale
    -- supervisor's credentials valid again.
    lease_epoch   BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),

    -- Quarantine is a state with a cause, not a badge. Both columns move together, enforced below.
    quarantine_reason TEXT,
    quarantined_at    TIMESTAMPTZ,

    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revision      BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),

    CONSTRAINT quarantine_has_a_reason CHECK (
        (status = 'QUARANTINED') = (quarantine_reason IS NOT NULL)
        AND (quarantine_reason IS NULL) = (quarantined_at IS NULL)
    ),
    UNIQUE (id, workspace_id)
);

COMMENT ON COLUMN runner.session_key IS
    'Digest of the physical interactive desktop identity. The exclusivity key for lease admission. '
    'Never a process, container or host identifier: those differ between two supervisors sharing '
    'one screen, which is the case this column exists to make impossible.';

COMMENT ON CONSTRAINT quarantine_has_a_reason ON runner IS
    'A quarantined runner must say why, and a reason must be dated. Quarantine drives an operator '
    'decision about whether a desktop is safe to reuse; an undated reasonless quarantine is the '
    'cosmetic UI state the module prompt forbids.';

-- One runner per physical desktop, among the ones still in service. Revoked runners are retained
-- for audit and excluded here, so a desktop can be re-enrolled after its old registration is
-- revoked -- but not while that registration is live.
CREATE UNIQUE INDEX IF NOT EXISTS runner_one_per_physical_session
    ON runner (workspace_id, session_key)
    WHERE revoked_at IS NULL;

-- Enrollment credentials: short lived, single use, workspace bound.
--
-- Only the digest is stored. A stored enrollment token is a credential at rest that grants the
-- right to become a runner in a tenant, and the database is the wrong place for it -- module 03
-- established the same discipline for session tokens.
CREATE TABLE IF NOT EXISTS runner_enrollment_token (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    token_digest  TEXT NOT NULL UNIQUE CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    expires_at    TIMESTAMPTZ NOT NULL,
    -- Single use: set on redemption, and the redeeming statement requires it to be NULL.
    redeemed_at   TIMESTAMPTZ,
    redeemed_by_runner UUID,
    created_by    UUID NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (redeemed_by_runner, workspace_id) REFERENCES runner (id, workspace_id)
);

CREATE TABLE IF NOT EXISTS runner_preflight (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    runner_id     UUID NOT NULL,
    -- The epoch this preflight was performed under. A preflight cannot vouch for a later epoch: a
    -- new lease means a new attempt on that desktop and a fresh proof is required.
    lease_epoch   BIGINT NOT NULL CHECK (lease_epoch >= 0),
    runner_profile_digest   TEXT NOT NULL CHECK (runner_profile_digest ~ '^[0-9a-f]{64}$'),
    environment_config_digest TEXT NOT NULL CHECK (environment_config_digest ~ '^[0-9a-f]{64}$'),
    manifest_digest TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    -- The server's conclusion, computed from checks, never copied from the submission. There is no
    -- column here a runner can set to make itself ready.
    successful    BOOLEAN NOT NULL,
    refusal_summary TEXT NOT NULL,
    checks        JSONB NOT NULL,
    observed      JSONB NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (runner_id, workspace_id) REFERENCES runner (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id)
);

COMMENT ON COLUMN runner_preflight.successful IS
    'Derived server side from the checks column by PreflightResult.is_successful(). A runner submits '
    'observations; readiness is a conclusion drawn from them.';

CREATE TABLE IF NOT EXISTS desktop_lease (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    runner_id     UUID NOT NULL,
    -- Denormalized from runner deliberately: the uniqueness below must be expressible as an index
    -- on this table, and a lease must remain attributable to a desktop even if the runner row is
    -- later re-enrolled under a different identity.
    session_key   TEXT NOT NULL CHECK (session_key ~ '^[0-9a-f]{64}$'),
    run_id        UUID NOT NULL,
    attempt_id    UUID NOT NULL,
    epoch         BIGINT NOT NULL CHECK (epoch >= 1),

    -- Wall clock, for audit and for the server's own expiry sweep. The supervisor's stopping
    -- decision uses a monotonic deadline held in its own process; these two are not interchangeable
    -- and neither is derived from the other.
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline_at   TIMESTAMPTZ NOT NULL,
    heartbeat_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- How this lease ended. NULL means active, and active is what the uniqueness index keys on.
    released_at   TIMESTAMPTZ,
    release_reason TEXT CHECK (release_reason IN (
        'COMPLETED', 'STOP_ACKNOWLEDGED', 'EXPIRED_WITHOUT_STOP_PROOF', 'AMBIGUOUS_ACTION',
        'OPERATOR_RESET', 'SUPERSEDED_BY_RESET'
    )),

    -- Cancellation metadata rather than another Run.status (CONTRACTS section 4).
    cancel_requested_at     TIMESTAMPTZ,
    cancellation_revision   BIGINT,
    stop_acknowledged_at    TIMESTAMPTZ,
    -- The epoch that acknowledged the stop. Compared against `epoch`: an acknowledgement from a
    -- superseded supervisor proves nothing about the current one.
    stop_acknowledged_epoch BIGINT,

    CONSTRAINT release_has_a_reason CHECK ((released_at IS NULL) = (release_reason IS NULL)),
    CONSTRAINT cancellation_records_its_revision CHECK (
        (cancel_requested_at IS NULL) = (cancellation_revision IS NULL)
    ),
    CONSTRAINT acknowledgement_records_its_epoch CHECK (
        (stop_acknowledged_at IS NULL) = (stop_acknowledged_epoch IS NULL)
    ),
    FOREIGN KEY (runner_id, workspace_id) REFERENCES runner (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id),
    -- An epoch is issued once per runner. Two leases claiming the same epoch would make a stale
    -- supervisor indistinguishable from the current one.
    UNIQUE (runner_id, epoch)
);

-- INV-10, enforced by the database rather than by application sequencing.
--
-- Keyed on session_key, so it spans runner rows: if the same physical desktop were somehow
-- registered twice, both registrations would still contend for this one index entry. Keyed on
-- workspace_id too, because tenancy is part of every key here; a cross-tenant desktop collision is
-- not possible anyway, since a desktop belongs to one workspace.
CREATE UNIQUE INDEX IF NOT EXISTS desktop_lease_one_active_per_physical_session
    ON desktop_lease (workspace_id, session_key)
    WHERE released_at IS NULL;

-- At most one active lease per run as well, so a run cannot hold two desktops.
CREATE UNIQUE INDEX IF NOT EXISTS desktop_lease_one_active_per_run
    ON desktop_lease (run_id)
    WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS desktop_lease_expiry_sweep
    ON desktop_lease (deadline_at)
    WHERE released_at IS NULL;

-- The durable action journal.
--
-- CONTRACTS section 7: "Store action intent before sending to the OS." The intent row is written
-- and committed first; the result arrives afterwards or does not arrive at all. A row with an
-- intent and no result is the ambiguous case, and it is ambiguous *because* the record exists --
-- without it, a crash between dispatch and result would be indistinguishable from no action at all.
CREATE TABLE IF NOT EXISTS runner_action (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    lease_id      UUID NOT NULL,
    run_id        UUID NOT NULL,
    attempt_id    UUID NOT NULL,
    epoch         BIGINT NOT NULL CHECK (epoch >= 1),
    -- Supervisor-assigned, contiguous from 1 within an attempt. Distinct from the canonical evidence
    -- sequence that module 04's sequencer allocates: this is the producer's own ordering.
    action_sequence INTEGER NOT NULL CHECK (action_sequence >= 1),

    action        TEXT NOT NULL,
    key_chord     TEXT,
    -- Text is recorded because an ambiguous TYPE_TEXT has to be describable to a human deciding what
    -- happened. Fixture values are synthetic by construction (module 06 refuses credential shapes).
    text_value    TEXT,
    origin        TEXT NOT NULL,

    intent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ,
    result_at     TIMESTAMPTZ,
    result_status TEXT CHECK (result_status IN ('SUCCEEDED', 'FAILED', 'AMBIGUOUS')),
    result_detail TEXT,

    -- Set when this action's outcome was never established. Never cleared by a retry, because there
    -- is no retry: INV-09 forbids blindly repeating an ambiguous desktop action.
    ambiguity_reason TEXT,

    CONSTRAINT result_is_complete CHECK ((result_at IS NULL) = (result_status IS NULL)),
    -- Both directions. The one-way version of this check allowed an AMBIGUOUS action with no reason
    -- recorded, which is the worst of the three states to be in: it reads as a known-bad result in
    -- any query filtering on status while telling an operator nothing about what is unknown, and it
    -- is the state a caller reaches by writing the row directly instead of through
    -- mark_action_ambiguous, which is also what quarantines the desktop.
    CONSTRAINT ambiguity_is_a_result CHECK (
        (ambiguity_reason IS NOT NULL) = (result_status = 'AMBIGUOUS')
    ),
    CONSTRAINT dispatch_follows_intent CHECK (dispatched_at IS NULL OR dispatched_at >= intent_at),
    FOREIGN KEY (lease_id, workspace_id) REFERENCES desktop_lease (id, workspace_id)
        ON DELETE CASCADE,
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (run_id, attempt_id, action_sequence)
);

COMMENT ON CONSTRAINT ambiguity_is_a_result ON runner_action IS
    'An ambiguity reason without an AMBIGUOUS status would let a known-bad action read as a normal '
    'one in any query filtering on status.';

-- Operator interventions. Reset is the only door out of quarantine, and it is audited.
CREATE TABLE IF NOT EXISTS runner_reset (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    runner_id     UUID NOT NULL,
    -- The epoch the desktop was fenced at. Everything up to and including this epoch is dead.
    fenced_epoch  BIGINT NOT NULL CHECK (fenced_epoch >= 0),
    requested_by  UUID NOT NULL,
    -- Whether the reset actually proved the old actor cannot act. A failed reset leaves the runner
    -- quarantined; it does not hand the desktop to the next run.
    succeeded     BOOLEAN NOT NULL,
    proof         JSONB NOT NULL,
    local_impact_warning TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (runner_id, workspace_id) REFERENCES runner (id, workspace_id) ON DELETE CASCADE
);

COMMENT ON COLUMN runner_reset.local_impact_warning IS
    'What a reset does to the physical desktop, recorded with the request. An operator resetting a '
    'machine someone may be sitting at is entitled to have been told so in writing.';

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'runner', 'runner_enrollment_token', 'runner_preflight', 'desktop_lease', 'runner_action',
        'runner_reset'
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
