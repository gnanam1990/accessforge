# Prompt 28 — End-to-end release proof, documentation and pilot package

**Dependencies:** 27; additionally 09 and 20 for full R1  
**Requirements:** FR-001–FR-025; INV-01–INV-16  
**Owns:** release evidence manifest, setup/user/operator documentation, event artifact preparation and pilot-validation plan

Read [SESSION-HEADER.md](SESSION-HEADER.md), [PRD.md](../PRD.md), [CONTRACTS.md](../CONTRACTS.md), [TEST-PLAN.md](../TEST-PLAN.md), [IMPLEMENTATION-PLAN.md](../IMPLEMENTATION-PLAN.md) and [SOURCES.md](../SOURCES.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 28 only.

Objective: Demonstrate the actual delivered release from clean setup through usable evidence, then produce truthful implementation and evaluation artifacts. Do not equate an event submission package with a completed R1 product or validated business.

Inspect exact HEAD, dirty state, dependency handoffs, mandatory tests and current capability gates. Choose E0 or R1 explicitly. Unsupported NVDA, missing GitHub write proof or absent multi-workspace operations cannot disappear from an R1 claim because the E0 video looks good.

Implementation tasks:
1. Build a requirement-to-source-to-test-to-runtime matrix covering every applicable FR and invariant. Label implemented, partial, blocked, deferred and untested separately; include the reason for each exclusion.
2. Perform a fresh installation using only shipped documentation and configured authorized test accounts. No hidden SQL edits, manual state promotion or author-only environment shortcuts in the acceptance path.
3. Execute the real reference journey: authorize environment, freeze task, actual reader preflight, real Strands run, complete failed baseline, constrained patch approval, candidate build and independent rerun.
4. Run protected functional regressions and capture a real scoped human review. Do not invent disabled-user participation or treat a machine-generated review as human acceptance.
5. Export the actual evidence and verify it independently in a clean environment. Bind release manifest, commands, screenshots and recordings to the exact reviewed source/build/profile identities.
6. Include failure proof alongside the happy repair: unavailable reader or interrupted evidence produces INCONCLUSIVE; an unsafe validation-removing patch does not become VERIFIED. Demonstrate persistence after reload/restart.
7. Write complete installation, configuration, supported-matrix, user workflow, limitation, privacy, troubleshooting and operator-recovery documentation. Record licensing/provenance and third-party requirements accurately.
8. Prepare an architecture diagram and an accessible video plan showing actual UI, backend and AT evidence. Label any injected defect, absent optional media, gated integration and edited time sequence. Never synthesize a screen-reader recording as real evidence.
9. Recheck official event requirements, deadline/timezone, license, repository visibility, prior-work disclosure and allowed artifacts before final packaging. Draft submission text locally; do not publish, submit or accept terms without separate approval.
10. Prepare a permission-based pilot plan: observe three target operators, reproduce two real barriers including at least one authorized externally reported incident, and obtain consented review of a repair. Separate design-partner access from the commercial gate: a paid pilot or explicit commercial commitment. Unpaid collaboration and drafted outreach are not willingness-to-pay evidence.

Required verification:
Run all release-mandatory TEST-PLAN cases at the captured head. Report command outcomes, failed/skipped/blocked counts and exact platform matrix rather than a vague tests passed statement. Ask a fresh operator to follow the shipped workflow where available; absence of that person remains an explicit validation gap. Do not require or invent outreach, signatures or cloud access to complete the local artifact preparation.

Acceptance gate:
The declared release works through its actual central path and has honest, independently inspectable proof. E0 completion requires actual VoiceOver, Strands, application/backend, candidate verification and human-review evidence. R1 additionally requires its full operational/platform/integration matrix. Remaining gates prevent the corresponding readiness claim.

Handoff:
Write docs/handoffs/28.md with exact release claim, evidence manifest, commands/results, missing approvals and unresolved blockers. Hand the package to module 29 for independent challenge; do not self-certify final readiness or submit anything. Stop after this module.
```
