-- Preserve caller-operation identity beyond the generic 24-hour idempotency cache.
-- Legacy requests remain inspectable by ID; their unknown caller key is never invented.
ALTER TABLE diagnosis_request ADD COLUMN operation_key_digest TEXT
 CHECK(operation_key_digest IS NULL OR operation_key_digest ~ '^[0-9a-f]{64}$');
ALTER TABLE diagnosis_request ADD CONSTRAINT diagnosis_request_operation_unique
 UNIQUE(workspace_id,requested_by,run_id,operation_key_digest);
