# Prompt 19 — Replayable events and bounded scheduling

**Dependencies:** 04, 18  
**Requirements:** FR-015, FR-017, FR-021; INV-07–INV-11, INV-13, INV-14  
**Owns:** durable UI event streams, schedule admission and worker coordination

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), the durable-job design in [TDD.md](../TDD.md) and relevant [TEST-PLAN.md](../TEST-PLAN.md) cases.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 19 only.

Objective: Keep product state correct through reconnects and repeated deliveries, and schedule only currently authorized bounded test work. E0 may expose manual runs only when that limitation is declared; replayable event correctness is still required. R1 includes schedules.

Inspect the transactional outbox, job records, leases and public operation APIs. State which transport is selected locally and in the proposed hosted configuration. Neither transport becomes the authoritative source of business state.

Implementation tasks:
1. Publish committed domain events from the outbox to workspace-scoped SSE streams. Use durable event IDs and an explicit resume contract; timestamps alone are not replay cursors.
2. Authorize connection and continued delivery. Handle membership/role revocation, session expiry and workspace changes without leaking subsequent events or another tenant's historical payloads.
3. Implement Last-Event-ID replay, bounded retention, explicit replay-gap/reset responses and authoritative snapshot resynchronization. Reconnection itself must never imply a run completed.
4. Make consumers idempotent for duplicate events and robust to delayed publication. Persist processing progress without losing committed events on publisher restart.
5. Implement versioned schedules referencing explicitly approved ExecutionGrant records with project/environment, allowed journey/policy versions, source-ref selector, safe effects, budgets, expiry and revision. A schedule cannot broaden its grant or reuse an exact-run Approval.
6. At each occurrence resolve immutable inputs, allocate run/authorization IDs, seal a manifest and mint a fresh exact RUN_EFFECTS child recording parent ID/revision and issuing service identity. Recheck parent scope/revision/expiry/revocation, membership and quota at both minting and dispatch. Changed inputs require a new child; grants never authorize patches or GitHub publication.
7. Deduplicate schedule occurrences transactionally and define missed-run behavior, timezones and overlap limits. Default to no concurrent attempt on one interactive desktop; never create a burst of old runs after downtime.
8. Record cancellation request/revision, deny new admission and request the runner fence. CANCELLED requires no admitted action or current-epoch stop acknowledgement without unresolved action. Lost acknowledgement/ambiguous action means INTERRUPTED plus quarantine. Budget exhaustion and already-performed test effects remain visible.
9. Add admission backpressure, fair per-workspace concurrency and runner-unavailable explanations. Avoid unbounded retries when physical desktop preflight cannot pass.
10. Provide operator pause/resume controls and a safe manual trigger that uses the identical admission contract. Do not implement outbound webhooks or notifications without a separately specified, authorized need.

Required verification:
Create crash tests around outbox publication, duplicate occurrence, worker death and stale lease. Revoke/change a parent grant between minting and dispatch; mutate child inputs; attempt grant-based patch/publication. Test cancellation with lost acknowledgement/in-flight ambiguity and SSE expired-session/old/foreign cursors. Use real PostgreSQL/local transport; label hosted proof separately.

Acceptance gate:
A browser reconnects after an actual API/worker interruption and reconstructs correct state without false success. For R1, one explicitly authorized schedule runs once per admitted occurrence with a real runner; otherwise mark schedule runtime proof blocked. No recurring user automation is installed outside the product by this prompt.

Handoff:
Write docs/handoffs/19.md with transport, event replay and crash evidence, schedule scope, commands/results and next eligible modules. Stop after this module.
```
