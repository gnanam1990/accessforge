# Baseline action approval cancellation

The authenticated baseline runner now supplies a fresh AbortSignal to
`authorizePhysicalAction(command, signal)`. The per-action physical budget,
explicit runner cancellation, completion and failure close that scope. Completion
closes it before waiting for the server result acknowledgement.

After asynchronous physical preflight/retention, cancellation is checked before
opening approval. A late preflight can no longer invoke a stale operator callback.
Physical desktop-claim and independent-effect-observer wrappers forward the same
signal and check it before and after the trusted callback. Existing server
admission, origin checks, effect permits and one-shot dispatch remain required.

Operator implementations must honor the signal for their own async work and
recheck it before effects. This is cooperative cancellation, not a mechanism for
terminating arbitrary operator code or an already dispatched SDK input. It grants
no startup/action consent and supplies no missing native runtime observations.

TypeScript build and 20 focused synthetic authenticated-runner checks passed
after the failure-path follow-up. The original patch also passed 13 preflight,
bootstrap and startup-authority checks. Coverage includes timeout signal delivery, explicit cancellation,
fresh signals on successive actions, completed-scope closure and no approval
after late retained preflight. A failed result-journal flush also closes approval
before waiting for recovery acknowledgement. No actual VoiceOver/NVDA startup, model call,
live database migration or deployment occurred. Native private provisioning,
stale-input measurement and full physical acceptance remain unfinished.
