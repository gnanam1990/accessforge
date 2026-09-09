# Prompt 16 — Attributable human review and finding lifecycle

**Dependencies:** 03, 15  
**Requirements:** FR-009, FR-011, FR-012, FR-014; INV-07, INV-08, INV-11, INV-12  
**Owns:** human-review domain services, scoped attestations and append-only review records

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [PRD.md](../PRD.md) and the review acceptance cases in [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 16 only.

Objective: Let an authorized human assess a particular repair and its limitations without rewriting machine evidence, implying legal certification or collecting unnecessary disability information.

Inspect existing tenant roles, candidate verification records and finding reducers. Confirm which exact authenticated role may create each review, and whether the proposed reviewer has access to the underlying evidence.

Implementation tasks:
1. Define Review records with workspace, reviewer identity/role, exact patch and verification digests, journey version, environment, observations, limitations, verdict and timestamp. Validate required fields and reject unknown security-sensitive fields.
2. Support ACCEPT, CHANGES_REQUESTED and UNABLE_TO_ASSESS explicitly. Do not coerce an absent reviewer, unsupported environment or interrupted review into acceptance.
3. Create review requests only for accessible target records. Treat a requested review and a submitted attestation as separate events; assignment alone does not establish participation. Enforce configured independent-review policy using canonical actor identity; another session or alias must not bypass a self-review restriction.
4. Bind submissions to If-Match revision and immutable target digests. Stale source/patch/verification changes require a new review context rather than silently carrying acceptance forward.
5. Enforce finding lifecycle rules. RESOLVED requires a verified repair and required human review. A valid reviewer may dismiss or reopen a finding with a reason, but cannot rewrite the original run or convert INCONCLUSIVE to PASS.
6. Preserve all review history as append-only records, including corrections or superseding assessments. Separate a reviewer's subjective observation from the deterministic run outcome in every serialized view.
7. Require no demographic or disability disclosure for normal product use. If a separate user study is enabled later, require its own consent record; never derive consent from workspace membership.
8. Limit review authority to review actions. ACCEPT is not RUN_EFFECTS, PATCH_APPLY, GITHUB_PUBLISH, deployment approval or credential delegation.
9. Provide safe evidence-access queries, redacted attachments and clear inaccessible/deleted-evidence states. A reviewer must be able to say UNABLE_TO_ASSESS when necessary source material is unavailable.
10. Add audit events for request, submission, supersession, dismissal and reopening. Notifications or external messages are not sent in this module; expose internal events for later integrations.

Required verification:
Create negative tests for cross-workspace review access, wrong role, revoked membership during submission, stale digest, repeated idempotency key with changed body, accepted review of an INCONCLUSIVE candidate and a missing artifact. Prove that a review cannot mutate Run.outcome or issue an execution approval. Test duplicate submission, concurrent reviewers and append-only correction handling against real PostgreSQL. Verify sensitive optional fields are neither required nor inferred.

Acceptance gate:
A real authorized person can inspect one verified candidate and submit an attributable scoped assessment. The product stores what that person actually reported and preserves machine proof separately. Do not fabricate a disabled-user test, invent reviewer credentials or claim user validation from an automated test account. Missing human participation remains an explicit release-proof blocker.

Handoff:
Write docs/handoffs/16.md with exact reviewed digests, role-enforcement evidence, commands and actual results. State independently whether the workflow is implemented, a human review occurred and the reviewer tested the product using assistive technology. Redact participant data; stop after this module.
```
