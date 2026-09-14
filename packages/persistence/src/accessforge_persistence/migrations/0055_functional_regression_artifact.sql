-- Original protected regression results are outcome-bearing, retained JSON artifacts.
ALTER TABLE evidence_artifact DROP CONSTRAINT evidence_artifact_kind_check;
ALTER TABLE evidence_artifact ADD CONSTRAINT evidence_artifact_kind_check CHECK(kind IN (
 'SPEECH_TRANSCRIPT','ACTION_TRACE','RUNNER_JOURNAL','PREFLIGHT_RECORD','EFFECT_RECEIPT',
 'DIAGNOSTIC_LOG','SCREENSHOT','MODEL_RUNTIME','FIXTURE_SETUP','FUNCTIONAL_REGRESSION'
));
