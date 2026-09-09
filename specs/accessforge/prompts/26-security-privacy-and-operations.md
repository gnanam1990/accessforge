# Prompt 26 — Security hardening, privacy, metering and operational control

**Dependencies:** 17, 19, 25  
**Requirements:** FR-014, FR-015, FR-019–FR-021, FR-025; INV-01–INV-16  
**Owns:** cross-cutting security/retention policy, usage entitlement, operational settings UI/API, telemetry and incident procedures

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [SECURITY-PRIVACY.md](../SECURITY-PRIVACY.md), [TEST-PLAN.md](../TEST-PLAN.md) and operating design in [TDD.md](../TDD.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 26 only.

Objective: Turn the working path into a supportable, bounded system without overstating its isolation or privacy. R1 includes measured usage and administrator-managed entitlements, not payment collection, card data, taxation or automated overage charges.

Inspect actual process identities, credential locations, filesystem/network boundaries and data flow. Treat documentation that says isolated as a hypothesis until an adversarial probe demonstrates the enforced restriction.

Implementation tasks:
1. Produce a source-bound threat model covering untrusted websites/repositories/models, build workers, desktop sessions, API, queues, evidence, integrations and administrators. Map enforcement to concrete code/process/IAM boundaries and residual risks.
2. Audit capability separation end to end. Confirm navigator cannot reach DOM/source/observer data and build/repair processes cannot reach control-plane secrets, metadata services or publication credentials through alternative routes.
3. Implement data minimization, consent gates and explicit retention classes for source, fixture references, speech, recordings, diagnostics, model input/output and review data. Default to synthetic accounts and private evidence.
4. Add tested redaction before logs/model/export exposure where policy requires it. Redaction is not assumed perfect: document supported patterns, limitations and prohibited sensitive workflows.
5. Implement scoped deletion across primary records/objects, derived views, caches and supported export storage. Preserve required minimal audit facts where justified and disclose backup expiry plus downloaded-copy limits. Deletion invalidates affected completeness claims.
6. Enforce per-workspace action, wall-time, model and runner budgets through durable admission/accounting. Model-provided usage is not the sole trusted billing input; distinguish measured, estimated and unavailable units.
7. Implement idempotent usage metering and manually configured entitlement revisions. Concurrent jobs must not overspend admitted limits; do not add checkout, automatic top-up, money movement or invented monetary savings. Wire /usage and settings APIs to the authoritative records, not estimated dashboard constants.
8. Add structured redacted telemetry for queue age, lease health, reader/preflight failure, evidence gaps, model failures, budget rejection and export integrity. Avoid recording raw task content or high-cardinality secrets in metrics.
9. Build quarantine/recovery procedures preserving cancellation-request versus actual-stop proof and ambiguous effects. Implement accessible settings for membership, retention/deletion, budgets/usage/entitlement, ExecutionGrant scope/expiry/revocation and exact GitHub payload approvals using real APIs. Enforce owner/read-only views and stale revisions. Grant revocation blocks both child minting and dispatch, not merely future schedule creation.
10. Add abuse/rate limits, dependency/security scanning and privilege-revocation drills. Disclose E0's dedicated local-desktop boundary and R1's exact supported isolation model rather than claiming a hosted fleet was hardened.

Required verification:
Run cross-tenant attacks at API/row/job/runner/object/SSE/export boundaries, stale credential/revocation tests, secret canaries in each capture path, malicious build egress probes, concurrent quota admission and duplicate usage events. Test deletion with an existing export and restore policy. Exercise settings through keyboard/actual reader, including denied roles and changed revision. Verify incidents contain enough diagnosis without secret leakage. External penetration testing and professional legal advice are separate unfulfilled gates unless actually completed.

Acceptance gate:
The declared deployment mode's critical boundaries withstand concrete probes and quota/privacy behavior is observable through real services. Any bypass capable of granting unauthorized execution or a false PASS blocks release rather than becoming a documentation caveat.

Handoff:
Write docs/handoffs/26.md with threat-model evidence, commands/results, residual risks, metering semantics and independent-review gaps. No paid resources or external incident notifications are created implicitly. Stop after this module.
```
