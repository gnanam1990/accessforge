-- Module 12: privacy-safe, authoritative navigator planning checkpoints.
--
-- This is deliberately not a serialized Strands conversation. Chat contains reader announcements
-- and can grow into a second business database. The retained record is a closed set of model-call
-- identity, proposed-action metadata and supervisor dispatch status. There is no column for DOM,
-- screenshots, source, observer receipts, assertion expectations, raw typed text or announcement
-- text.

ALTER TABLE run_attempt
    ADD CONSTRAINT run_attempt_run_workspace_key UNIQUE (id, run_id, workspace_id);

CREATE TABLE navigator_planning_checkpoint (
    id             UUID PRIMARY KEY,
    workspace_id   UUID NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    run_id         UUID NOT NULL,
    attempt_id     UUID NOT NULL,
    run_ref        TEXT NOT NULL CHECK (btrim(run_ref) <> ''),
    checkpoint_sequence BIGINT GENERATED ALWAYS AS IDENTITY,
    kind           TEXT NOT NULL CHECK (kind IN (
        'MODEL_CALL_STARTED', 'ACTION_PROPOSED', 'ACTION_RESOLVED', 'MODEL_CALL_STOPPED',
        'NAVIGATOR_STOPPED'
    )),
    recorded_at    TIMESTAMPTZ NOT NULL,

    sdk_version    TEXT,
    provider       TEXT,
    model_id       TEXT,
    action         TEXT CHECK (action IN (
        'NEXT', 'PREVIOUS', 'ACTIVATE', 'TYPE_TEXT', 'KEY_CHORD', 'READ_CURRENT',
        'WAIT_FOR_READER_IDLE', 'STOP'
    )),
    key_chord      TEXT,
    text_value_ref TEXT,
    dispatch_status TEXT CHECK (dispatch_status IN (
        'SUCCEEDED', 'FAILED', 'AMBIGUOUS', 'REFUSED'
    )),
    action_id      UUID,
    stop_reason    TEXT CHECK (stop_reason IN (
        'COMPLETED', 'SDK_LIMIT', 'PROVIDER_TIMEOUT', 'CANCELLED',
        'CONTEXT_BUDGET_EXHAUSTED', 'PROVIDER_ERROR'
    )),

    CONSTRAINT navigator_checkpoint_shape CHECK (
        (kind IN ('MODEL_CALL_STARTED', 'MODEL_CALL_STOPPED', 'NAVIGATOR_STOPPED'))
            = (model_id IS NOT NULL)
        AND (kind IN ('MODEL_CALL_STARTED', 'MODEL_CALL_STOPPED', 'NAVIGATOR_STOPPED'))
            = (provider IS NOT NULL)
        AND (kind IN ('MODEL_CALL_STARTED', 'MODEL_CALL_STOPPED', 'NAVIGATOR_STOPPED'))
            = (sdk_version IS NOT NULL)
        AND (kind IN ('ACTION_PROPOSED', 'ACTION_RESOLVED')) = (action IS NOT NULL)
        AND (kind = 'ACTION_RESOLVED') = (dispatch_status IS NOT NULL)
        AND (kind IN ('MODEL_CALL_STOPPED', 'NAVIGATOR_STOPPED')) = (stop_reason IS NOT NULL)
        AND (action = 'KEY_CHORD') = (key_chord IS NOT NULL)
        AND (action = 'TYPE_TEXT') = (text_value_ref IS NOT NULL)
        AND NOT (key_chord IS NOT NULL AND text_value_ref IS NOT NULL)
    ),
    FOREIGN KEY (run_id, workspace_id)
        REFERENCES run (id, workspace_id) ON DELETE CASCADE,
    FOREIGN KEY (attempt_id, run_id, workspace_id)
        REFERENCES run_attempt (id, run_id, workspace_id) ON DELETE CASCADE,
    UNIQUE (workspace_id, attempt_id, checkpoint_sequence)
);

CREATE INDEX navigator_planning_checkpoint_attempt_order
    ON navigator_planning_checkpoint (workspace_id, attempt_id, checkpoint_sequence);

ALTER TABLE navigator_planning_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE navigator_planning_checkpoint FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON navigator_planning_checkpoint
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

COMMENT ON TABLE navigator_planning_checkpoint IS
    'Bounded model-call and action metadata. Not a chat transcript and never reader content, raw '
    'fixture text, DOM, source, screenshots, observer receipts or assertion expectations.';
