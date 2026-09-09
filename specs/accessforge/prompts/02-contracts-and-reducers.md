# Prompt 02 — Shared schemas and deterministic state reducers

**Dependencies:** 01. **Requirements:** FR-002, FR-003, FR-007, FR-015, FR-016. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), the complete [CONTRACTS.md](../CONTRACTS.md), relevant TDD sections and TEST-PLAN. Schema definitions in this module govern every subsequent implementation.

## Copy-paste prompt

```text
Implement AccessForge module 02 only.

Objective: Make run identity, state transitions and evidence validity explicit across Python and TypeScript before implementing orchestration.

Owns: packages/contracts/, packages/domain/, contract fixtures and reducer/property tests. Do not add persistence, API authorization or platform drivers in this module.

Required inputs: Completed module 01 handoff and the unchanged canonical vocabulary. JSON Schema 2020-12 is authoritative; generated bindings are outputs, never independently maintained competing schemas. Use canonical FR and INV IDs in test names or metadata.

Tasks:
1. Define strict schemas for sealed RunManifest, journeys/assertions, runner profiles, scoped approvals, ExecutionGrant/child links, producer source records and closing watermarks, canonical evidence envelopes, outcomes and public state records. Keep producer provenance inside payload; reject unknown security-sensitive fields.
2. Generate Python/TypeScript bindings through reproducible commands and commit generation fixtures. Validate at trust boundaries at runtime; static typing is not input validation.
3. Implement RFC8785 canonicalization and lower-case SHA-256 digest helpers in both languages. Use shared vectors covering property order, escaping, Unicode, timestamps, negative zero and permitted numbers; reject unsafe integer overflow.
4. Enforce status/outcome pairs: nonterminal→NOT_EVALUATED; COMPLETED→PASS/FAIL/INCONCLUSIVE; INTERRUPTED→INCONCLUSIVE; CANCELLED before admitted execution→NOT_EVALUATED, after start→INCONCLUSIVE. Test the entire cross-product; add no new states. Missing evidence cannot become FAIL merely because an agent describes a problem.
5. Implement pure transition reducers using admitted events and expected revisions. Cancellation first sets cancelRequestedAt/cancellationRevision; CANCELLED requires current-epoch stopAcknowledgedAt and no unresolved action, except a provably never-admitted queued run. Ambiguity ends INTERRUPTED with quarantine.
6. Implement this exact precedence: invalid/incomplete identity, preflight or evidence OR any UNKNOWN required assertion/completion condition means INCONCLUSIVE; otherwise any FALSE required assertion/completion condition means FAIL; otherwise all required conditions TRUE means PASS. UNKNOWN outranks a simultaneous FALSE.
7. Model exact approvals, parent ExecutionGrant scope and child-authorization validity against digest, revision, expiry and revocation. Grant-derived children bind exact resolved runs and issuing service identity; grants never authorize patches/publication. PATCH_APPLY never grants merge/deploy permission.
8. Establish immutable terminal records and append-only corrections/review records. A retry has a new runId and fixture instance with a link to the prior run, not a resumed terminal record.
9. Encode baseline/candidate comparison prerequisites without deciding verification from model confidence. Specify permitted build differences explicitly; protected assertions and authorization are never patchable.
10. Publish documented migration/versioning rules and contract examples for downstream persistence, runner, evaluator and web consumers. Fail CI when generated bindings drift from schemas.

Negative tests: Unknown authority fields, forged workspace, stale/revoked parent grant, wrong child digest, premature CANCELLED, simultaneous FALSE/UNKNOWN, false completion, changed assertion digest, incomplete observed failure, unsupported capability, terminal rewrite, reviewer-forced PASS, integer overflow and mismatched cross-language digest. Property-test that no arbitrary event sequence reaches PASS with missing required evidence (INV-02 through INV-06, INV-08, INV-11, INV-12, INV-13, INV-16).

Acceptance: Both languages validate identical fixtures and compute identical digests; every published transition has permitted and denied tests; outcome truth-table tests cover all branches without a model or database. These tests prove domain semantics only, not operating-system integration.

Stop conditions: Raise an ADR and reconcile every impacted spec if a required contract is missing or contradictory. Do not invent a permissive default, change canonical states or hide ambiguity in free-form JSON.

Handoff: Write docs/handoffs/02.md listing schema versions, generation commands, invariants demonstrated and consumers ready for modules 03, 04 and 06. Include actual test output summaries and unresolved design questions.
```
