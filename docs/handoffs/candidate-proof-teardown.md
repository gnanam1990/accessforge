# Candidate proof cleanup closure

The first-profile candidate runner now attempts owned reader cleanup after a startup attempt
that rejects, since startup may already have changed reader state. Cleanup completes before
`RUN_FINISHED` and the closing watermark. Failed cleanup produces `INTERRUPTED`, not a completed
candidate proof; it requires desktop reconciliation before another run. A successful explicit
STOP still avoids duplicate teardown.

Validation: desktop-runner TypeScript build and nine focused synthetic candidate-runner tests
passed locally. No physical reader action, acoustic capture or profile qualification is claimed.
This does not add a startup timeout or a complete physical candidate provisioner.

Read-only host preflight on 2026-09-15 found matching macOS/Safari 26.6, an unlocked console and
Accessibility permission. VoiceOver Utility showed AppleScript control OFF; Automation remained
UNKNOWN. No dedicated desktop binding was supplied. These observations do not establish a ready
or qualified session. No model-provider credential was found in the inspected process environment
or project `.env` provider-key entries; other credential stores were not inspected.
