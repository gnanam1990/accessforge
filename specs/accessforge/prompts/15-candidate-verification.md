# Prompt 15 — Matched candidate verification and protected regressions

**Dependencies:** 11, 14  
**Requirements:** FR-002, FR-007, FR-010, FR-011; INV-03–INV-06, INV-11, INV-16  
**Owns:** candidate verification coordinator, comparison records and protected regression gates

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [TDD.md](../TDD.md) and the applicable [TEST-PLAN.md](../TEST-PLAN.md) sections.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 15 only.

Objective: Establish whether an approved source patch fixes the reproduced behavior without changing the task, weakening application protections or substituting a different execution environment.

Inspect the actual checkout and dependency handoffs first. Identify the immutable baseline, exact patch approval and current evaluator implementation. An earlier PASS report is not reusable proof unless all required identities and validity checks match.

Implementation tasks:
1. Define a verification record linking the complete failed baseline, patch digest, candidate build, fresh candidate run, functional-regression results and all referenced manifests. Preserve separate execution, outcome and patch states.
2. Compare source/build/environment identities before dispatch. Permit only the approved patch and explicitly recorded legitimate build differences. Reject unexplained browser, reader, evaluator, fixture, locale or assertion drift.
3. Reset the authorized application fixture and independent observer for each run. Baseline and candidate use equivalent versioned fixture definitions, not the same already-consumed account/session state.
4. Recheck PATCH_APPLY approval scope, target digest, revision, expiry and revocation before candidate construction. Never infer authorization to publish, merge or deploy from candidate approval.
5. Freeze required assertions and observer contracts outside the patchable tree. Run protected functional tests for validation, authorization, successful backend submission and failed-input handling.
6. Schedule the candidate through the existing actual AT runner path. Do not let the repair agent choose a shortcut policy or feed DOM/source/observer information into its navigator.
7. Implement deterministic patch transitions through BUILDING and VERIFYING. VERIFIED requires a complete valid matched pair, required producer watermarks and protected regressions; unknown completion is INCONCLUSIVE and cannot qualify. Failed verification retains the original finding and all evidence.
8. Preserve cancellation request versus acknowledged stop, stale-lease, timeout and build-failure facts. Missing stop proof or ambiguous action is INTERRUPTED/quarantined, not CANCELLED. Reruns create fresh linked runs/fixtures. Freeze repetition policy before execution; report all attempts instead of selecting one favorable result.
9. Emit a human-readable comparison explaining the exact assertion changed, evidence consulted, permitted differences and limitations. No compliance score or universal-accessibility assertion.
10. Expose a read-only verification summary for API, UI and human-review consumers without granting them mutation authority over the frozen outcome.

Required verification:
Write failing cases first for a patch that removes a required field, bypasses authorization, changes protected assertions, modifies the observer, swaps a reader profile, uses a stale approval or reuses a consumed fixture. Cover an incomplete candidate with an apparently successful receipt, missing baseline artifacts, duplicate completion and cancellation during verification. Exercise real PostgreSQL transitions and actual candidate build execution. Unit adapter fakes do not satisfy the AT integration gate.

Acceptance gate:
On the reference application's real backend, reproduce the baseline failure, apply only the approved patch and rerun using the pinned actual VoiceOver profile. Show the frozen assertions and functional regressions succeeding without forbidden navigation. If the physical runner or model path is unavailable, mark runtime proof BLOCKED while retaining implemented deterministic tests; do not mark the repair VERIFIED.

Handoff:
Write docs/handoffs/15.md with exact identities, commands, observed results, failure cases and next eligible modules. Human review remains a separate dependency before REVIEW_ACCEPTED or finding RESOLVED. Stop after this module.
```
