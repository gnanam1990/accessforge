# Prompt 24 — Patch decisions, matched verification and human evidence review

**Dependencies:** 15, 16, 17, 21  
**Requirements:** FR-010–FR-013, FR-019, FR-020; INV-03–INV-05, INV-07, INV-08, INV-11, INV-12, INV-15, INV-16  
**Owns:** source-diff review, approval preview, baseline/candidate comparison, human attestation and export screens

Read [SESSION-HEADER.md](SESSION-HEADER.md), [UI-UX.md](../UI-UX.md), [CONTRACTS.md](../CONTRACTS.md) and corresponding [TEST-PLAN.md](../TEST-PLAN.md) cases.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 24 only.

Objective: Help a human make a specific, informed decision about a proposed repair. Do not compress patch application, machine verification, human acceptance and external publication into one ambiguous Approve button.

Inspect the actual patch, verification, review and export APIs. Reuse shared components and server-owned state reducers. List the authorization boundary of each action before wiring its button.

Implementation tasks:
1. Build a patch workspace showing exact base source, patch digest, changed files, evidence-grounded rationale and the reproduced finding. Treat source/model text as untrusted; escape HTML and dangerous links.
2. Offer an accessible unified diff and plain-text alternative with file navigation and line labels. A visual side-by-side diff alone is insufficient for screen-reader review.
3. Show PATCH_APPLY approval scope, expiry and exact isolated-candidate effect, including Does not merge or deploy. Protected-file modification is blocked, not an ignorable warning. Reconfirm changed revisions/digests; a stale browser preview cannot approve different content.
4. Render BUILDING, VERIFYING, VERIFIED, REVIEW_ACCEPTED and failed/stale/rejected paths separately. No animated progress bar may conceal an unavailable build worker or incomplete runner evidence.
5. Present baseline/candidate comparison with matched identity checks, permitted differences, frozen assertion changes, actual transcript links and protected functional-regression outcomes.
6. Display failed protected checks prominently, including patches that remove validation or authorization. A green accessibility assertion does not override a failing functional requirement.
7. Build human review forms for ACCEPT, CHANGES_REQUESTED and UNABLE_TO_ASSESS with tested context, observations and limits. Explain that review is separately attributable and does not rewrite machine outcomes.
8. Require no demographic/disability disclosure. Make optional study participation visibly separate from ordinary review and never infer consent from a checked workspace role.
9. Build export preview showing included records, redactions, missing/deleted artifacts, verification limits and intended disclosure. Request private export through the API; do not publish a share link automatically.
10. Present publication as unavailable or a separate GITHUB_PUBLISH workflow when module 20 exists. Human acceptance and candidate approval are never labeled as merge/deployment permission.

Required verification:
Test source content containing hostile HTML, stale patch while approval dialog is open, double-click approval, missing baseline artifact, candidate INCONCLUSIVE, regression failure, unauthorized reviewer, revoked membership, redacted export and changed target after review. Verify keyboard-only diff navigation, dialog focus return, linked error summaries and real screen-reader attestation flow. Use actual API records; placeholder verified patches do not satisfy acceptance.

Acceptance gate:
An authorized person can inspect a real candidate, understand the exact evidence and limitations, record an actual scoped assessment and obtain a private export. The UI cannot turn an INCONCLUSIVE repair into VERIFIED or treat absent human participation as completed review.

Handoff:
Write docs/handoffs/24.md with actual reviewed identities, approval-boundary tests, keyboard/AT observations, commands/results and remaining external/human gates. Stop after this module.
```
