# Prompt 07 — Desktop enrollment, leases and dispatch control

**Dependencies:** 04, 06. **Requirements:** FR-004, FR-005, FR-014, FR-015, FR-021. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD runner protocol and TEST-PLAN. A server lease is necessary but insufficient to fence an interactive desktop.

## Copy-paste prompt

```text
Implement AccessForge module 07 only.

Objective: Admit exactly one authorized attempt to a physical interactive session and keep unknown desktop state unavailable until reset and preflight establish safety.

Owns: runner registry, enrollment/lease services, dispatch policy, apps/desktop-runner/supervisor protocol, runner control integration tests. Actual VoiceOver and NVDA adapters belong to modules 08 and 09.

Required contracts: OFFLINE/PREFLIGHT_REQUIRED/READY/BUSY/QUARANTINED states, monotonic lease epochs, one active lease per physical interactive session, sealed manifest and approval revalidation. Test adapters may exercise protocol code but cannot satisfy actual AT release gates.

Tasks:
1. Implement enrollment with short-lived single-use workspace-bound credentials and explicit device/session identity. Record platform capabilities and revocation; do not accept self-asserted READY as successful preflight.
2. Define a structured preflight result binding reader/browser versions, locale, keyboard layout, permission state, desktop ownership, target environment and runnerProfileDigest.
3. Implement transactional lease admission keyed by physical interactive session, not merely a process/container ID. Concurrent supervisors sharing the same desktop cannot acquire separate admissible attempts.
4. Revalidate manifest, exact RUN_EFFECTS authorization, any parent ExecutionGrant revision/scope/revocation, expiry, effects, budgets and runner profile before dispatch. Missing prerequisites fail closed; a standing grant alone cannot dispatch a run.
5. Implement a supervisor action gate that checks local lease validity, current epoch, cancellation, budget and action allowlist before each OS action. Use monotonic local timing for lease deadlines while retaining UTC audit timestamps.
6. Persist action intent before command dispatch through the durable protocol. An action acknowledgement timeout is ambiguous; never resend TYPE_TEXT, ACTIVATE or another potentially consequential command blindly.
7. Persist cancelRequestedAt/cancellationRevision and stop new admission. Accept stopAcknowledgedAt only for the current epoch after in-flight actions resolve. Admit CANCELLED only then, or for a provably never-admitted queued run. Lost acknowledgement/ambiguous action becomes INTERRUPTED with ambiguityReason and quarantine; server expiry is not stop proof.
8. Keep an expired or uncertain session quarantined until a trusted reset proves previous activity cannot continue and actual-reader preflight succeeds. Do not immediately assign its desktop to another run.
9. Implement bounded queues, backpressure and capability matching. Unsupported NVDA or VoiceOver profiles return visible unavailable reasons, never silent fallback to virtual/browser-only execution.
10. Provide operator reset/inspection interfaces with audit records and explicit local-desktop impact warnings. No automatic process killing outside the runner's own scoped session.

Negative tests: Two supervisors race on one session; stale epoch submits results/stop acknowledgement; revoked grant child dispatches; offline runner acts after expiry; cancel arrives between intent and dispatch; acknowledgement/result is lost; clock jumps; profile lies; reset fails; old attempt sends late evidence. Prove cancellation requested is not terminal CANCELLED (INV-01, INV-06 through INV-10, INV-13, INV-14).

Acceptance: PostgreSQL concurrency tests and supervisor protocol tests show one admitted attempt, fail-closed action gating and quarantine after ambiguity. Mark actual OS fencing UNVERIFIED until module 08/09 runs its physical-boundary tests; do not claim protocol mocks prove desktop containment.

Stop conditions: If physical-session identity, reliable local fencing or safe reset cannot be established, block automatic reuse of that runner. Do not reduce quarantine to a cosmetic UI state.

Handoff: Write docs/handoffs/07.md with lease diagrams, crash/cancel results, reset contract, exact unverified OS boundaries and the adapter interfaces required by modules 08 and 09.
```
