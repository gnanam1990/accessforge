# Live VoiceOver last-phrase channel probe

Scope: original module 08 / build-flow audit F1. The concrete Guidepup physical bootstrap now
overrides supplied speechCaptureWorking with a live native channel query, instead of treating a
configuration boolean or the SDK's cached speech log as proof of a responsive capture channel.

The native helper finds exactly one running VoiceOver process, checks its Apple code identity,
and addresses a read-only get-data Apple Event to that PID. Existing Automation permission must
be available with askUserIfNeeded=false; sending uses neverInteract and a one-second timeout.
The installed VoiceOver.sdef supplies the read-only last-phrase/content property codes (lapr/lptx).
There is no application-name launch path or output/copy/save command. Process termination or
identity change, permission refusal, error, empty content or an oversized phrase returns UNKNOWN.
Successful output contains only a fixed channel-responsive boolean, never phrase text or its hash.

The TypeScript port validates the owned executable, enforces a 1.5-second/1-KiB process boundary,
honors cancellation and accepts only the exact successful receipt. It does not inherit product,
model or observer credentials. The existing physical-preflight clock and assigned desktop checks
surround the query. A dedicated operator-owned desktop remains required; this is not isolation
from malicious same-user code or a general cross-session audit service.

This is point-in-time channel responsiveness with nonempty current content, **not proof of new
speech, acoustic output, capture freshness for an action, or a final verdict**. Action evidence
still uses its own retained provenance and conservative sample checks. The first post-start probe
can remain unavailable if VoiceOver has not supplied any phrase; no synthetic speech or silent
startup action is inserted to force readiness. Helper-specific Automation/TCC configuration may
be needed on the real runner; this change does not grant it.

Validation: native Swift type-check and optimized binary compilation pass; runner TypeScript
compilation passes; eight focused tests pass (synthetic executable process cases plus original
unqualified-bootstrap refusal). Both helpers were compiled locally but neither was executed.
No actual VoiceOver query/startup, OS permission modification, model call or deployment occurred.

Remaining C1 gaps include concrete stale-input-source measurement and complete private operator
setup/action configuration. Actual host qualification, runtime evidence and full G2 acceptance
remain unproven, as does independent forbidden-effect monitoring (C2).
