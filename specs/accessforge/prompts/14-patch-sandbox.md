# Prompt 14 — Constrained patch proposal and isolated candidate build

**Dependencies:** 05, 13. **Requirements:** FR-002, FR-010, FR-014, FR-015. **Release:** E0 and R1 with different hosting trust boundaries.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD build-worker threat model and TEST-PLAN. E0 permits an owned local application, not hosted execution of arbitrary customer code.

## Copy-paste prompt

```text
Implement AccessForge module 14 only.

Objective: Turn an evidence-grounded repair brief into a reviewable patch and isolated candidate build without giving repository code, package scripts or the agent access to control-plane authority.

Owns: apps/build-worker/, patch proposal services, sandbox policy, candidate artifact preparation and containment tests. Module 15 owns independent repair verification. No GitHub push, merge, deployment or production mutation is included.

Required inputs: Immutable source/environment identity from module 05 and bounded repair brief from module 13. Use PATCH_APPLY approval bound to exact base SHA, patch digest, revision and expiry. This approval authorizes only the isolated candidate workspace.

Tasks:
1. Create a task-specific immutable source snapshot and disposable candidate workspace with exact base identity. Preserve user checkout changes; never edit the user's working branch to simulate isolation.
2. Generate a structured patch proposal limited to approved application paths. Reject modifications to evaluator, protected tests, fixture oracle, consent, security policy, authorization, CI secrets and execution tooling.
3. Inspect patch paths and content for traversal, symlink escapes, binary replacement and dependency/script changes. Treat dependency, lockfile or build-policy changes as separately reviewed scope, not routine accessibility edits.
4. Persist PROPOSED status and exact patch/base digests. Recheck current PATCH_APPLY approval at dispatch; stale base, changed bytes, expiry or revocation blocks application.
5. Execute source/build scripts only inside the documented containment boundary with resource limits, bounded scratch storage and restricted network access. Containers under one unrestricted host identity do not automatically prove credential isolation.
6. Keep cloud, GitHub, observer, signing, production and control-plane credentials unavailable to the patch agent and build sandbox. Deny instance-metadata access and generic host mounts; dependency fetching follows explicit allowlisted reproducible policy.
7. Apply the approved patch once to the isolated snapshot, record process/output provenance and produce a candidate artifact digest. Do not trust a build's self-reported SHA or PASS output without supervisor verification.
8. Run protected functional regressions from outside patch-writable paths. Detect disabling validation, bypassing authorization, suppressing errors or skipping task steps as forbidden repairs even if accessibility assertions would become easier.
9. Handle timeout, cancellation, crash and cleanup with durable patch/build states. Ambiguous build outcome cannot become VERIFIED; retain bounded failure diagnostics and safely retire only task-owned scratch resources.
10. Prepare comparison inputs for module 15: base/candidate source trees, exact approved diff, matched dependency/environment configuration, build artifacts, regression results and permitted build differences. Publish no success claim beyond a built candidate.

Negative tests: Malicious package script reads host credentials; network request reaches metadata; patch modifies protected assertion; symlink escapes workspace; changed base after approval; build forges artifact identity; dependency lock changes; validation is removed to pass the task; stale worker uploads output (INV-03 through INV-08, INV-11, INV-16).

Acceptance: An authorized patch produces a provenance-bound candidate while adversarial fixture code cannot reach forbidden resources. Actual containment tests, not an architecture diagram, establish the boundary. VERIFIED remains unavailable until module 15 completes matched independent reruns.

Stop conditions: If containment cannot be demonstrated, restrict execution to the explicitly owned E0 fixture and mark hosted untrusted-source support blocked. Never execute arbitrary customer build scripts directly on the host to keep progress moving.

Handoff: Write docs/handoffs/14.md with patch/artifact identities, exact authority scope, containment evidence, functional regressions, cleanup results and candidate inputs ready for module 15.
```
