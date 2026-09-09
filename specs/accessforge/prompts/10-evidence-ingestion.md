# Prompt 10 — Ordered evidence ingestion and artifact integrity

**Dependencies:** 04, 07, 08. **Requirements:** FR-006, FR-014, FR-015, FR-020. **Release:** E0 and R1; accepts 09 when available.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD evidence protocol and TEST-PLAN. Do not make NVDA availability a dependency that blocks the E0 VoiceOver evidence path.

## Copy-paste prompt

```text
Implement AccessForge module 10 only.

Objective: Turn actual runner output into ordered, provenance-bound evidence without treating an agent-authored PASS field or uploaded file name as a trusted result.

Owns: evidence ingestion service, packages/persistence/evidence/, private artifact intake/quarantine, run-evidence assembly and integrity tests. Deterministic outcome admission belongs to module 11; independent export packaging belongs to module 17.

Required inputs: Module 04 journal/locks, module 07 leases, actual module 08 source records and module 09 when supported. Preserve the canonical envelope unchanged: producer provenance is nested inside payload; producerSequence is not canonical sequence. The local action journal remains separate.

Tasks:
1. Authenticate producer and bind workspace/run/attempt/epoch/manifest. Enforce event-kind ACLs: assigned supervisor supplies reader/action/preflight/lifecycle records; independent observer alone supplies EFFECT_RECEIPT and observer ASSERTION_OBSERVATION. Reject caller-selected canonical sequence/hash and forged producer identity.
2. Validate sourceRecordId, producerSequence, sourceRecordDigest and sourceRecord. Deduplicate by producer/sourceRecordId/digest; reject conflicts. Stage each producer's gaps until contiguous and retain original sourceTime; no client chooses receivedTime.
3. Serialize one trusted sequencer per attempt under a database lock/transaction. After admission assign eventId, canonical sequence and previousEventHash; nest validated producer provenance in payload. Compute payload/canonical hashes using module 02 vectors, excluding receivedTime/self-hash as specified.
4. Preserve canonical idempotency and uniqueness without equating a contiguous canonical chain with complete input. Require authenticated closing watermarks and declared artifacts for every required producer; missing interior records or tails block finalization.
5. Acknowledge admitted action intent durably before OS dispatch and retain the supervisor's separate fsynced journal as evidence. Lost action-result acknowledgement remains ambiguous; never ask the supervisor to replay an effect blindly.
6. Upload artifacts into workspace-scoped quarantine using short-lived narrowly bound authorization. Enforce maximum size, content type, safe object names, server-side hashing and binding to the correct manifest before promotion.
7. Store raw and redacted object identities separately with explicit retention state. Avoid transcripts, form values and bearer URLs in logs or generic telemetry. Restrict diagnostics independently from safe navigator observations.
8. Assemble finalization prerequisites: canonical chain, every required producer watermark/contiguous source stream, start/finish, authorized preflight/observations, current identities and required artifacts. Evaluator-derived reader assertions must not masquerade as observer-authored records. Return invalid/incomplete reasons, not an outcome guess.
9. Handle stale late events and cancelled/interrupted runs without rewriting terminal records. Retain rejected-arrival audit metadata safely; a late RUN_FINISHED cannot resurrect an interrupted run.
10. Expose evidence summaries and paginated replay queries under tenant authorization. Provide bounded ingest buffers/backpressure; resource exhaustion must remain visible and cannot discard required evidence while preserving a green result.

Negative tests: Concurrent valid producers; source gap/tail loss despite contiguous canonical events; altered sourceRecordId replay; caller-selected canonical hash; supervisor-forged receipt; observer OS action; stale watermark; corrupted/swapped artifact; cross-workspace URL; stale epoch; oversized transcript (INV-03, INV-06, INV-07, INV-11, INV-14, INV-15).

Acceptance: Actual module 08 output and independent observer records are serialized without producer forks; tampering, wrong producer authority and missing tails block completeness. Recovery preserves source provenance and canonical hashes. Integrity attests authenticated ingestion, not physical truth or universal human experience.

Stop conditions: Missing actual producer evidence limits integration status. Unavailable object storage prevents required-artifact finalization; never replace it with an empty success object or omit the artifact requirement.

Handoff: Write docs/handoffs/10.md with real trace identity, integrity vectors, persistence/restart results, redaction limits and the finalization API consumed by module 11.
```
