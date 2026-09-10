-- Module 10: artifact quarantine, raw/redacted identities, retention state and finalization gates.
--
-- The canonical chain from modules 03 and 04 attests that the sequencer admitted records in an order.
-- It says nothing about the files those records refer to. This migration adds the half that does, and
-- every column here exists to make one specific lie impossible to tell.
--
-- The central one: an artifact whose JSON says PASS must not become trusted because it was uploaded.
-- So an artifact arrives QUARANTINED, its bytes are hashed *by the server*, and it is promoted only
-- after that hash, its declared type and its manifest binding all check out. The digest column is
-- what the server computed, never what the uploader claimed.

CREATE TABLE IF NOT EXISTS evidence_artifact (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    attempt_id    UUID NOT NULL,

    -- What this file is. A closed vocabulary, because "the evaluator requires a speech transcript"
    -- has to be checkable, and it cannot be if a producer names its own kinds.
    kind          TEXT NOT NULL CHECK (kind IN (
        'SPEECH_TRANSCRIPT', 'ACTION_TRACE', 'RUNNER_JOURNAL', 'PREFLIGHT_RECORD',
        'EFFECT_RECEIPT', 'DIAGNOSTIC_LOG', 'SCREENSHOT'
    )),

    -- The producer that uploaded it, bound to the attempt's epoch. A file from a superseded
    -- supervisor is not this attempt's evidence.
    producer_id   TEXT NOT NULL,
    lease_epoch   BIGINT NOT NULL CHECK (lease_epoch >= 0),

    -- Server-side, always. A client-supplied digest would let an uploader name a hash for bytes it
    -- did not send, which is the whole attack this column defends against.
    content_digest TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    content_type  TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL CHECK (size_bytes > 0),

    -- The object key inside the workspace's prefix. Never a URL: a stored URL is a bearer
    -- credential if it is presigned and a cross-tenant reference if it is not.
    object_key    TEXT NOT NULL,

    -- Bound before promotion. An artifact that cannot be tied to the sealed manifest is a file
    -- someone uploaded, not evidence about a particular run of particular bytes (INV-03).
    manifest_digest TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),

    state         TEXT NOT NULL CHECK (state IN ('QUARANTINED', 'PROMOTED', 'REJECTED')),
    rejected_reason TEXT,

    -- Retention is explicit, never inferred from a missing row. INV-15: a deletion must not leave an
    -- export that still looks fully verifiable, so the record survives its bytes and says so.
    retention     TEXT NOT NULL DEFAULT 'RETAINED'
        CHECK (retention IN ('RETAINED', 'REDACTED', 'DELETED')),
    retention_changed_at TIMESTAMPTZ,
    retention_reason TEXT,

    -- The redacted view is a different object with a different digest. Same file with a flag would
    -- mean one digest describing two different byte streams, and any later verification would be
    -- checking the wrong one.
    redacted_object_key TEXT,
    redacted_digest TEXT CHECK (redacted_digest IS NULL OR redacted_digest ~ '^[0-9a-f]{64}$'),

    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at   TIMESTAMPTZ,

    CONSTRAINT rejection_has_a_reason CHECK ((state = 'REJECTED') = (rejected_reason IS NOT NULL)),
    CONSTRAINT promotion_is_dated CHECK ((state = 'PROMOTED') = (promoted_at IS NOT NULL)),
    -- Both halves of the redacted identity, or neither. One without the other is a view nobody can
    -- verify or a digest of nothing.
    CONSTRAINT redaction_is_a_pair CHECK (
        (redacted_object_key IS NULL) = (redacted_digest IS NULL)
    ),
    -- A non-default retention state must say when and why. "REDACTED" with no reason is a fact
    -- nobody can audit, and retention changes are exactly what an auditor reads.
    CONSTRAINT retention_change_is_explained CHECK (
        retention = 'RETAINED' OR (retention_changed_at IS NOT NULL AND retention_reason IS NOT NULL)
    ),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (id, workspace_id),
    -- One upload per (attempt, kind, digest). A byte-identical re-upload is a retry and collapses;
    -- different bytes for the same kind are two artifacts and both are kept.
    UNIQUE (attempt_id, kind, content_digest)
);

COMMENT ON COLUMN evidence_artifact.content_digest IS
    'Computed by the server from the bytes it received. Never a client-declared value: an uploader '
    'able to name the hash of its own upload could name the hash of bytes it did not send.';

COMMENT ON COLUMN evidence_artifact.redacted_object_key IS
    'The redacted view is a separate object with its own digest, not a flag on this row. One digest '
    'describing two byte streams would make later verification check the wrong one.';

CREATE INDEX IF NOT EXISTS evidence_artifact_by_attempt
    ON evidence_artifact (attempt_id, kind) WHERE state = 'PROMOTED';

-- Which artifact kinds a given journey requires. Declared per run rather than hardcoded, because a
-- journey with no functional assertion needs no functional trace, and a requirement that is always
-- the same list is a requirement nobody can tighten for a journey that needs more.
CREATE TABLE IF NOT EXISTS required_artifact (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    kind          TEXT NOT NULL,
    producer_id   TEXT NOT NULL,
    declared_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    UNIQUE (run_id, kind, producer_id)
);

-- Records that arrived and were not admitted. Retained because "we received this and refused it" is
-- itself evidence, and because a late RUN_FINISHED trying to resurrect an interrupted run is an
-- event an operator should be able to see rather than infer from silence.
--
-- Deliberately narrow: an id, a reason, a digest and a timestamp. Not the payload. A rejected record
-- may contain a transcript, a form value or a token, and storing it to be helpful would put
-- unvalidated content from an unauthenticated source into the audit trail.
CREATE TABLE IF NOT EXISTS rejected_arrival (
    id            UUID PRIMARY KEY,
    workspace_id  UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id        UUID NOT NULL,
    attempt_id    UUID,
    producer_id   TEXT NOT NULL,
    source_record_id TEXT,
    event_type    TEXT,
    payload_digest TEXT CHECK (payload_digest IS NULL OR payload_digest ~ '^[0-9a-f]{64}$'),
    reason_code   TEXT NOT NULL CHECK (reason_code IN (
        'RUN_ALREADY_TERMINAL', 'STALE_EPOCH', 'PRODUCER_NOT_AUTHORIZED', 'SOURCE_RECORD_CONFLICT',
        'UNKNOWN_ATTEMPT', 'WATERMARK_ALREADY_CLOSED', 'BUFFER_FULL'
    )),
    reason_detail TEXT NOT NULL,
    arrived_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, workspace_id) REFERENCES run (id, workspace_id) ON DELETE CASCADE
);

COMMENT ON TABLE rejected_arrival IS
    'Audit metadata for records that arrived and were refused. Deliberately excludes the payload: a '
    'rejected record may carry a transcript, a form value or a token, and storing it to be helpful '
    'would move unvalidated content from an unauthenticated source into the audit trail.';

CREATE INDEX IF NOT EXISTS rejected_arrival_by_run ON rejected_arrival (run_id, arrived_at);

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['evidence_artifact', 'required_artifact', 'rejected_arrival'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %I USING (workspace_id = current_workspace_id()) '
            'WITH CHECK (workspace_id = current_workspace_id())', t);
    END LOOP;
END
$$;
