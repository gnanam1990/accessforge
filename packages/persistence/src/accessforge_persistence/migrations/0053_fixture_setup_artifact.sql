-- Required original setup evidence must be admitted by the durable artifact boundary too.
-- Extend the closed kind list; preserve all historical artifacts and their original bytes.
ALTER TABLE evidence_artifact DROP CONSTRAINT evidence_artifact_kind_check;
ALTER TABLE evidence_artifact ADD CONSTRAINT evidence_artifact_kind_check CHECK(kind IN (
 'SPEECH_TRANSCRIPT','ACTION_TRACE','RUNNER_JOURNAL','PREFLIGHT_RECORD','EFFECT_RECEIPT',
 'DIAGNOSTIC_LOG','SCREENSHOT','MODEL_RUNTIME','FIXTURE_SETUP'
));
