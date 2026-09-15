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

This .mjs module is trusted operator code, **not untrusted configuration or a sandbox**.
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
