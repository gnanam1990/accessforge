-- Never infer a historical build's daemon from today's Docker context.
ALTER TABLE candidate_build_attempt ADD COLUMN daemon_endpoint TEXT;
ALTER TABLE candidate_build_attempt ADD COLUMN daemon_id TEXT;

-- Older in-flight attempts cannot be safely dispatched/reconciled without a recorded endpoint.
-- Preserve historical receipts, but fence unfinished legacy attempts rather than guessing.
UPDATE candidate_build_attempt
   SET state = 'UNKNOWN', epoch = epoch + 1, finished_at = clock_timestamp(),
       failure_code = 'MISSING_DAEMON_BINDING'
 WHERE state IN ('CLAIMED', 'DISPATCHED');

ALTER TABLE candidate_build_attempt ADD CONSTRAINT candidate_daemon_pair CHECK (
    (daemon_endpoint IS NULL) = (daemon_id IS NULL)
);
ALTER TABLE candidate_build_attempt ADD CONSTRAINT candidate_daemon_endpoint_local CHECK (
    daemon_endpoint IS NULL OR daemon_endpoint ~ '^unix:///[^[:cntrl:]]+$'
);
ALTER TABLE candidate_build_attempt ADD CONSTRAINT candidate_daemon_id_valid CHECK (
    daemon_id IS NULL OR daemon_id ~ '^[A-Za-z0-9:-]{1,128}$'
);
ALTER TABLE candidate_build_attempt ADD CONSTRAINT active_candidate_requires_daemon CHECK (
    state NOT IN ('CLAIMED', 'DISPATCHED') OR daemon_endpoint IS NOT NULL
);
COMMENT ON COLUMN candidate_build_attempt.daemon_endpoint IS
    'Operator-selected explicit local Unix endpoint. Immutable with the original claim; never '
    'reconstructed from ambient Docker context. Legacy null bindings cannot dispatch.';
