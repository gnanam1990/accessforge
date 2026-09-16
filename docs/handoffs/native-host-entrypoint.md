# Explicit operator native host entrypoint

Scope: original module 08 and build-flow audit F1. The shipped runner now accepts
`--native-host PRIVATE_OPERATOR.mjs --handoff-file NEW_PRIVATE_PATH`. Without arguments it
retains the existing diagnostic behavior. With arguments, the verified-reader gate runs
before any module import, so the empty qualification matrix still refuses execution.

The module exports `provisionNativeHost(signal)` returning NativeHostConfiguration from
the runner's native-host module: privateDirectory, bootstrap, and navigator configuration.
Navigator configuration must provide the concrete independentObserver configuration;
the launcher owns cancellation and does not accept a replacement signal. Bootstrap retains
the existing startup-consent reference, exact lease identity, runtime/action callbacks,
journal, private directories and sealed Safari target requirements.

For the active `codex-chatgpt` model profile, navigator `environment` must explicitly
provide `ACCESSFORGE_DATABASE_URL`, an absolute operator `HOME`, and a `PATH` containing
only absolute search directories. Optional `CODEX_HOME` and `TMPDIR` must also be absolute.
Select the operator's existing Codex login store; never copy authentication tokens into
this module. The parent environment is not inherited. AWS credentials, API keys and other
environment entries are rejected before physical bootstrap. The Python proposal adapter
passes only its narrower OAuth/path allowlist to Codex, not the database credential.
The native process entrypoint accepts only the complete, bounded Codex profile. Retired Bedrock,
partial/unknown profiles and mixed provider fields are refused before reader bootstrap, not
deferred until the Python child starts. Historical evidence readers remain compatible.

The focused process test exercises explicit path forwarding through a real child process
and refusal of missing/relative paths or foreign credentials before bootstrap. It uses a
synthetic child, not a model, and is not native reader or authenticated Codex acceptance.

This .mjs module is trusted operator code, **not untrusted configuration or a sandbox**.
For an already-reserved baseline reference fixture, bootstrap can now supply
`referencePreparation: { fixture, authorize }`. It requires the existing live artifact probe
and an exact match to the sealed Safari URL/build. The runner measures the deployed build,
reauthorizes, reconciles the reserved fixture and opens/verifies Safari within its original
startup desktop claim and deadline, before the ordinary reader preflight. The approval callback
is specifically for fixture reconciliation/browser setup, not a substitute for reader consent.
There is no second desktop claim, automatic reset or retry. Setup history fills missing evidence
only. Each preparation guard also observes the assigned console and process session, unlocked
screen, Accessibility and Automation grants before allowing the next step; a claim file alone
cannot authorize effects on a different active desktop. The original clock/claim guard runs again
after these synchronous probes. Unknown observations refuse preparation. Setup history fills gaps
only; fresh runtime negatives/unknowns and the current artifact measurement take precedence.
Stale-input measurement is still required, and the qualified-reader gate is unchanged.
The focused checks use synthetic host/measurement ports; no actual browser or reader was run.

The explicitly labelled `--candidate-proof` host also accepts `referencePreparation` for its
already-reserved qualification fixture. It runs once before the first physical preflight, after
the complete action list is validated, under the original candidate desktop claim and deadline.
The setup signal combines caller cancellation with the remaining candidate lifetime. Subsequent
action preflights never repeat setup; fresh runtime values and live artifact measurements take
precedence over the setup receipt. The same real console/process/lock/permission guards and
separate fixture authorization apply. Reader startup still needs its own fresh authorization.
This connects qualification setup but supplies neither a private operator provisioner nor
stale-input evidence and does not enroll a matrix. Focused checks cover synthetic orchestration
and refusal of invalid typing before setup approval; they are not a positive physical host run.

It executes with the user's host privileges, including its imports. Only load independently
reviewed operator code, never files returned by the navigator, a candidate build or a browser.
The launcher checks a canonical, private, owned regular module and parent directory; those
checks are not isolation from malicious same-user filesystem replacement. Provisioning must
honor cancellation and supply real probes; no example TRUE callbacks are provided.

After provisioning, the launcher starts the existing one-shot native listener and atomically
publishes its privateReference in an exclusive, mode-0600 file. It never overwrites an older
handoff or prints bearer tokens. The controller reads that reference into NativeStartTransport
for the original baseline dispatch. The launcher waits for the native completion promise;
process success indicates closed execution, not a PASS verdict. Signals request cancellation,
not physical STOP proof. The handoff and native claims remain for reconciliation.

Validation: TypeScript compile and six focused CLI/publication tests pass. Tests exercise actual
private file publication, overwrite/link refusal, directory privacy, and refusal before operator
code import under the currently unqualified profile. They do not demonstrate a positive native
host run, physical reader, provider or database acceptance.

Still required: an actual provisioner integrating live speech capture, stale input-source and
action authority with owned browser/fixture setup; qualification and model approval; complete
independent forbidden-effect coverage and final G2 evidence. An arbitrary host module is not
evidence those production integrations exist. No actual AT startup or billable call was made.
