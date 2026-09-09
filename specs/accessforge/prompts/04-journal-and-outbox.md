# Prompt 04 — Authoritative journal and transactional outbox

**Dependencies:** 02, 03. **Requirements:** FR-006, FR-014, FR-015, FR-021. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD persistence/recovery and TEST-PLAN. Queue delivery and Strands session history are not authoritative business state.

## Copy-paste prompt

```text
Implement AccessForge module 04 only.

Objective: Make accepted work, state changes and dispatch intent survive process loss without duplicating desktop effects or inventing completed work.

Owns: packages/persistence/, transactional outbox transport adapters, durable-job infrastructure, database migrations and restart/concurrency tests. Preserve module 03 identity migrations and module 02 reducers as the only domain transition authority.

Required contracts: PostgreSQL owns admissible state; queue messages carry references, not trusted mutable commands. Mutations use principal/workspace/route/idempotency-key binding; same canonical request replays and changed content conflicts. Terminal records remain immutable.

Tasks:
1. Implement tenant-constrained migrations for operations, runs/attempts, approvals, ExecutionGrant records and exact child links, producer source-record streams/watermarks, canonical events, jobs/outbox and idempotency. Store parent revision, issuing service identity and immutable child target digest.
2. Apply reducer-admitted state changes, revision increments, audit events and outbox creation within one database transaction. Never publish a queue message before its authoritative state exists.
3. Implement safe outbox claiming and redelivery. Delivery can occur more than once; consumers re-read state and deduplicate against a stable operation identity rather than assuming exactly-once queues.
4. Implement idempotency reservation/result storage with canonical request digest and bounded retention. Concurrent identical requests resolve to one accepted operation; replay rechecks current read authorization before returning stored data.
5. Provide revision/epoch checks and an attempt-scoped transaction lock for the single trusted evidence sequencer. Enforce producer/sourceRecordId dedupe separately from canonical sequence uniqueness. Stale workers cannot advance state; grants are rechecked when exact children are minted and dispatched.
6. Separate transport acknowledgement from business completion. Record scheduling, execution-start intent, finalization and terminal admission with enough durable evidence to diagnose a crash at each boundary.
7. Persist cancelRequestedAt/cancellationRevision before denying new admission. Store current-epoch stopAcknowledgedAt; admit CANCELLED only with resolved in-flight actions or proof a queued run never admitted one. Missing acknowledgement/ambiguity becomes INTERRUPTED plus quarantine, never assumed stopping. Preserve performed effects.
8. Add local and AWS transport interfaces with identical semantics. Use an explicitly configured local transport for tests; do not provision billable AWS infrastructure or claim AWS delivery was tested without authority and evidence.
9. Implement recovery inspection for abandoned jobs and ambiguous attempts. An uncertain desktop action becomes interrupted/quarantined for downstream handling, not automatically retried because a visibility timeout expired.
10. Add privacy-safe metrics for queue delay, stale leases, conflicts and recovery backlog. Provide controlled migration and restoration instructions; do not automatically downgrade schemas or destroy persisted evidence.

Negative tests: Kill before commit, after commit/before publish, after publish/before acknowledgement and after action intent; duplicate delivery, concurrent producer sequencing/claims, premature CANCELLED, stale stop acknowledgement, revoked parent grant, stale lease write, rollback, cross-tenant job and changed-body replay. Use actual PostgreSQL transactions for critical cases (INV-06 through INV-11, INV-13).

Acceptance: Repeated delivery admits one business operation; committed jobs are recoverable; stale workers are rejected; cancellation and ambiguity remain visible. No test may infer that database fencing alone stops an already-running operating-system command—module 07/08 must prove local action gating.

Stop conditions: Database unavailability fails closed for new consequential dispatch. Do not use an in-memory fallback, blindly replay ambiguous actions or mark terminal outcomes from queue acknowledgements.

Handoff: Write docs/handoffs/04.md with schema revision, crash-matrix results, actual database evidence and the durable APIs available to projects, runners and evidence ingestion.
```
