# Candidate proof cleanup closure

The first-profile candidate runner now attempts owned reader cleanup after a startup attempt
that rejects, since startup may already have changed reader state. Cleanup completes before
`RUN_FINISHED` and the closing watermark. Failed cleanup produces `INTERRUPTED`, not a completed
candidate proof; it requires desktop reconciliation before another run. A successful explicit
STOP still avoids duplicate teardown.

Validation: desktop-runner TypeScript build and nine focused synthetic candidate-runner tests
passed locally. No physical reader action, acoustic capture or profile qualification is claimed.
This does not add a startup timeout or a complete physical candidate provisioner.

Follow-up: the runner snapshots the input action array and nested text before validation and async
retention. Caller mutation during retention/startup cannot substitute unapproved text or append an
extra action. Ten focused synthetic candidate checks and TypeScript compilation pass; no real
reader execution is claimed by this follow-up.

The next qualification-path change replaces the saved preflight report with a mandatory fresh
probe callback. BEFORE_STARTUP permits only explicitly measured inactive/unknown reader and
capture checks; missing checks and every other non-TRUE gate still block. A separate mandatory
trusted startup-authorization callback must verify operator consent and reader-control setup.
AFTER_STARTUP requires all checks TRUE. BEFORE_ACTION probes again after durable action intent,
before the adapter call; timed-out late probes cannot dispatch after teardown. Each measurement
is retained with its phase. Callers must supply real physical probes and real authorization,
not copy the synthetic test callbacks. This intentionally changes the candidate embedding API;
there are no production call sites in the repository yet.

Validation: TypeScript compilation and 15 focused synthetic candidate checks. No permission
was changed and no reader was started. This still does not deliver the physical candidate
provisioner, startup timeout, production C1 controller configuration or real profile qualification.

Read-only host preflight on 2026-09-15 found matching macOS/Safari 26.6, an unlocked console and
Accessibility permission. VoiceOver Utility showed AppleScript control OFF; Automation remained
UNKNOWN. No dedicated desktop binding was supplied. These observations do not establish a ready
or qualified session. No model-provider credential was found in the inspected process environment
or project `.env` provider-key entries; other credential stores were not inspected.
