# Prompt 25 — Real fault laboratory and adversarial quality benchmarks

**Dependencies:** 12, 15, 22, 23, 24; additionally 09 for R1  
**Requirements:** FR-004–FR-013, FR-015, FR-019, FR-023; INV-01–INV-16  
**Owns:** reference-app fault scenarios, adversarial harness, benchmark corpus and evidence-based quality reporting

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [TEST-PLAN.md](../TEST-PLAN.md) and fault/evaluation design in [TDD.md](../TDD.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 25 only.

Objective: Challenge the product using real execution boundaries and independently known expected outcomes. Prove the difference between reproducible accessibility barriers, invalid infrastructure runs and patches that only make a superficial test pass.

Inspect actual runner/model/build integrations and test fixtures. State which cases are controlled injected defects, externally reported reproducible defects and clean negative controls. Never present a seeded defect as a discovered customer incident.

Implementation tasks:
1. Build versioned reference-application scenarios with a real backend: valid form completion, unannounced validation error, broken focus recovery, missing label and keyboard trap where supported by the selected profile. Keep task semantics and expected outcomes explicit.
2. Protect an independent oracle for each scenario. Freeze assertions and backend receipts outside the patchable tree; ensure neither navigator nor repair agent can read answer keys during its restricted role.
3. Add clean controls and unsupported/unobservable cases. A correct benchmark must measure false findings and INCONCLUSIVE behavior, not only reward discovering planted failures.
4. Exercise actual Strands tool execution against the pinned VoiceOver runner for E0. For R1 run the declared Windows/NVDA profile too; unsupported combinations remain excluded from claimed coverage.
5. Inject process death around ACTION_INTENT, lost OS response/stop acknowledgement, expired lease, competing session, interrupted upload and stale completion. Attack producer gaps/watermarks and supervisor-forged observer receipts despite a contiguous canonical chain. Verify rejection and quarantine through independent processes/persisted records.
6. Attack capability separation with page/repository prompt injection, forbidden keyboard chords, DOM/selector requests, observer leakage, unapproved origins and attempts to access shell or publication credentials.
7. Include malicious repairs that remove validation, bypass authorization, edit frozen tests, alter fixture reset, change evaluator code or merely suppress visible errors. These must never establish a verified repair.
8. Mutate the relevant guard/evaluator logic in isolated test branches or processes and prove the intended regression fails. A test that still passes after removing the protection is insufficient evidence for that invariant.
9. Report denominators, exact versions, repetitions, clean/defective/unsupported counts, timeouts, observed failures, false positives and unresolved ambiguity. Do not invent a global accuracy figure from a small controlled corpus.
10. Produce replayable benchmark commands and redacted artifacts bound to exact source/build/profile/evaluator identities. Preserve failure examples; do not curate the report down to only successful demonstrations.

Required verification:
Execute the mandatory fault cases in TEST-PLAN at actual process, database and desktop boundaries where specified. Distinguish unit, contract, local integration, real AT, real model and human-review evidence in the report. Run at least one clean control and one complete failure-to-approved-patch-to-candidate comparison through the actual UI/API path. If a capability cannot be exercised, record its case as blocked rather than skipped-success.

Acceptance gate:
The same harness demonstrates a valid PASS, a supported complete FAIL and an INCONCLUSIVE infrastructure/evidence failure, while rejecting at least one superficially successful but unsafe patch. An independent operator can rerun the commands and inspect persisted evidence.

Handoff:
Write docs/handoffs/25.md with corpus identities, commands/results, mutation kills, all failed/blocked cases and exact E0/R1 coverage. Do not claim universal accessibility or production readiness from benchmark completion. Stop after this module.
```
