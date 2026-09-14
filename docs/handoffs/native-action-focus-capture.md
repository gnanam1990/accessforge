# Post-action native focus capture

Trusted Safari runner provisioning can explicitly set `keyboardFocusEvidence: true`. Default
behavior is unchanged. The physical/bootstrap factories preserve this option through the existing
qualified, consent-bound path; it does not grant reader startup, permissions or input authority.

When enabled, the original adapter action/reader observation is followed by a bounded native focus
sample, before local reader retention and authenticated observation acknowledgement. The runner
checks its execution/fencing/deadline state again after sampling. The native method receives the
closed focus record as a separate private argument associated with the same server action command.
The ordinary reader callback and navigator projection do not receive focus metadata.

`createSafariObservationProbes` shares PID/launch identity, concurrency and refusal state across
origin and focus sampling. A newly started browser cannot supply post-action focus for an action
authorized against the previous browser. Origin checks still do not require focus fields; focus is
measured only when requested. All native reads use the existing exact fixture/version checks.

Configured probe failure interrupts the action as ambiguous and preserves fencing. Missing focus
is not replaced with reader speech or a successful condition. A late result after timeout cannot
publish evidence or unlock another action. This is conservative: unsupported Safari AX identifiers
must be qualified before enabling this collection on a real run. Failure does not prove physical
STOP; original desktop/reader recovery obligations still apply.

Verification: TypeScript build and 49 scoped synthetic runner/origin/focus checks passed. They
cover adapter/sample/retention/result ordering, separate private metadata, missing/late capture,
cross-probe process replacement and concurrent-read fencing. No real Safari/VoiceOver sampling,
paid model invocation, OS change, deployment or live migration occurred.

Next: original retained-focus reconstruction and a frozen deterministic predicate, plus profile
qualification and end-to-end physical acceptance. This wiring alone does not produce a focus PASS.
