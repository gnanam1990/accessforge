# AccessForge — Release Checklist and Evidence Record

Version 1.0 • 9 September 2026 • All boxes start unchecked. This is a future sign-off template, not evidence that a product exists or passed testing.

## 1. Identify the exact candidate

Fill these fields before reviewing. A material change to a bound field makes affected proof stale.

| Field | Recorded value |
|---|---|
| Release target | E0 / R1 — select one |
| Implementation checkout and source commit/tree digest | PENDING |
| Build artifact and lockfile digests | PENDING |
| Schema/evaluator/migration versions | PENDING |
| Environment/fixture/journey/assertion identities | PENDING |
| Exact reader/browser/OS profiles, locale/layout/verbosity | PENDING |
| Navigator policy and model configuration digests | PENDING |
| Manifest/baseline/candidate/proof bundle IDs | PENDING |
| Reviewer and decision timestamp, UTC | PENDING |
| Evidence storage and independent verification command | PENDING |
| Known failed, blocked and outside-scope cases | PENDING |

Do not mark a broad section complete based on a single happy-path video. Attach actual test/handoff evidence to each accepted item.

## 2. Scope and claims

- [ ] The selected E0/R1 scope matches [CONTRACTS.md](CONTRACTS.md) and [PRD.md](PRD.md).
- [ ] E0 states one owner-operated authorized application and one actual pinned VoiceOver profile; it does not imply R1 tenancy, NVDA or arbitrary hosted customer-code readiness.
- [ ] R1 additionally has actual supported VoiceOver and NVDA profile proof, complete multi-workspace operations, optional-by-user GitHub publication and bounded schedules.
- [ ] Mobile, native desktop and PDF remediation are labelled roadmap, not delivered.
- [ ] Marketing/UI/docs do not claim legal certification, universal accessibility, independent disabled-user validation that never happened, or invented customers/revenue.
- [ ] Fault-injected defects, synthetic test data, model fixtures and actual observed customer incidents are distinguishable.
- [ ] “Specified”, “implemented”, “tested”, “blocked” and “outside this release” are used accurately throughout the handoff.

## 3. Authority and project safety

- [ ] Repository, origins, fixtures, test-only effects and credential profile are explicitly authorized and exact-scope approvals recheck at dispatch.
- [ ] No real purchase, live application submission, mass email, production mutation, terms acceptance or CAPTCHA bypass is in the execution path.
- [ ] Navigator cannot access DOM, selectors, source, screenshots, observer secrets, shell, clipboard read or unsupported keyboard shortcuts.
- [ ] Diagnostic/repair capabilities cannot alter evaluator, assertions, consent, fixture oracle or their own authority.
- [ ] PATCH_APPLY authorizes only the isolated candidate workspace; merge/deployment/publication require distinct permission.
- [ ] Tenant isolation has negative tests across API, rows, jobs, runners, SSE, artifacts and exports; R1 has no unresolved cross-tenant route.
- [ ] Build code is untrusted and actual filesystem/network/credential boundaries were exercised, not merely described.
- [ ] Revocation, expiry, stale revision and changed digest tests passed at their dispatch boundaries.
- [ ] Every scheduled R1 run has an exact RUN_EFFECTS child derived from an explicitly approved current ExecutionGrant; mint/dispatch recheck parent scope/revision/revocation and no grant authorizes patch/publication.

Evidence: T-001–T-010, T-015–T-016, T-036–T-042, T-045–T-046, T-053, T-059–T-063.

## 4. Genuine assistive-technology execution

- [ ] Actual reader identity/version and supported interactive desktop profile are captured.
- [ ] Preflight detects reader absence, locked session, permissions, capture failure, wrong profile and failed reset.
- [ ] One physical interactive session admits at most one active attempt; expired sessions are fenced/reset before reuse.
- [ ] Action intent is durable before input; ambiguous OS action after crash is never automatically repeated.
- [ ] Cancellation request metadata is distinct from terminal CANCELLED; current-epoch stop acknowledgment and resolved in-flight actions are required except a proven never-admitted queued run. Unknown stop becomes INTERRUPTED/quarantined, not falsely stopped.
- [ ] The backend receipt belongs to the fresh fixture/run; prior successful state cannot satisfy the task.
- [ ] E0 has actual VoiceOver evidence; R1 additionally has its independent actual NVDA evidence.
- [ ] No requested real-reader test silently fell back to Playwright/DOM or canned speech output.

Evidence: T-008, T-011–T-022, T-035.

## 5. Evidence, outcome and repair integrity

- [ ] Every run binds exact source/build, journey/assertions, fixture/environment, reader profile, navigator/evaluator and model identities.
- [ ] One trusted sequencer admits authenticated producer records into the canonical chain; producer/type ACLs prevent supervisor-forged observer receipts and observer OS actions.
- [ ] Producer sequences and closing watermarks prove source completeness beyond canonical-chain contiguity; duplicate/conflict/fork/gap/missing-tail/stale-attempt tests passed.
- [ ] Artifacts are private, validated, hashed and completeness-checked before finalization.
- [ ] Invalid/incomplete evidence or any unknown required assertion/completion observation yields INCONCLUSIVE even when another known condition is false.
- [ ] With complete/known evidence, false task completion yields FAIL even when every other assertion is true; complete task receipt cannot override a false required assertion.
- [ ] Status/outcome cross-product is enforced: nonterminal NOT_EVALUATED; COMPLETED PASS/FAIL/INCONCLUSIVE; INTERRUPTED INCONCLUSIVE; CANCELLED pre-execution NOT_EVALUATED or after-start INCONCLUSIVE, never PASS/FAIL.
- [ ] Terminal runs are immutable; reruns/corrections have linked fresh identities.
- [ ] Candidate verification uses matched profiles/fixtures/assertions and only the approved patch/permitted recorded build differences.
- [ ] Functional/authentication/validation regressions passed; removing a requirement cannot count as a repair.
- [ ] Actual independent verifier output—not repair-agent self-assessment—establishes verification.
- [ ] Human review is separately attributable, scoped and cannot turn incomplete evidence into VERIFIED.
- [ ] Offline export verification succeeds for its declared view; redacted/deleted raw-evidence limitations remain visible.

Evidence: T-005–T-007, T-023–T-034, T-039, T-047–T-054.

## 6. Real product experience

- [ ] Fresh-user setup → baseline → finding → patch approval → candidate → review → export works against the real API/backend without hidden database edits.
- [ ] Critical paths work by keyboard and declared actual readers; accessible diff/review is not replaced by visual screenshots.
- [ ] Loading, empty, denied, expired/stale, failed, cancelled, interrupted, missing evidence and unavailable runner states are complete and honest.
- [ ] SSE reconnect explicitly resynchronizes gaps; pending/accepted is never displayed as successful completion.
- [ ] Responsive layouts, zoom, contrast, reduced motion and long real content preserve approvals, evidence and recovery actions.
- [ ] Reader status announcements are useful, non-color alternatives exist and recordings have usable transcript alternatives.
- [ ] Review participation does not require disability disclosure or user-study consent.
- [ ] API/SDK/CLI validate schemas, revisions, idempotency and current permission consistently.

Evidence: T-052, T-055–T-058, T-065–T-068, T-078.

## 7. Privacy, budgets and operational readiness

- [ ] Synthetic/test data is the default; capture/model-provider inputs and required consent are documented.
- [ ] Redaction tested source, speech, form values, URLs, screenshots, model errors, logs, support bundles and exports within declared support.
- [ ] Retention/deletion reaches supported primary/derived/cache/backup paths; downloaded-copy and backup-delay limitations are explicit.
- [ ] Consent withdrawal/revocation stops new collection; restore does not silently revive deleted evidence or expired authority.
- [ ] Resource budgets stop work visibly; no fake success or unlimited retry after exhaustion.
- [ ] Usage records are durable/deduplicated; entitlement changes and already-admitted work have explicit semantics.
- [ ] R1 contains no unimplemented payment/tax collection represented as working billing.
- [ ] Dependency outage, quarantine, incident investigation and restoration were exercised with safe telemetry.
- [ ] Clean installation uses pinned dependencies and no hidden developer credentials/cache assumptions.
- [ ] Migrations and backup/restore preserve uncertainty, event identities, tombstones, fencing and approvals.
- [ ] Capacity report names hardware/workload and serialized desktop limits; no unmeasured SLA/scale promise.

Evidence: T-037, T-041–T-043, T-061–T-064, T-069–T-074.

## 8. Test evidence and independent challenge

- [ ] Applicable tests from [TEST-PLAN.md](TEST-PLAN.md) are mapped to actual results, not merely source files.
- [ ] Unit/schema/property, real database concurrency, actual sandbox security, actual AT, human UI and operational proof are reported separately.
- [ ] Randomized seeds/minimized failures, process-kill timing, platform versions and command outputs are retained.
- [ ] No critical flaky case was hidden by rerunning until green; failed attempts remain in evidence.
- [ ] Boundary mutations fail for intended reasons; relevant surviving mutants are unresolved coverage blockers.
- [ ] Held-out scenarios include real/injected failures, no-defect controls and unavailable-reader/evidence conditions; denominators and exclusions are reported.
- [ ] Independent final reviewer reproduced the repair path and highest-risk crash, denial, gap and malicious-patch cases.
- [ ] Final blocker list is finite, severity-backed and connected to release scope; no known invariant violation is waived for the deadline.

Evidence: T-075–T-080 plus all applicable focused tests.

## 9. Event artifacts and submission boundary

The current planning deadline is **15 September 2026, 00:00 UTC / 05:30 IST**. Recheck [official dates](https://agentsforhumans.devpost.com/details/dates), [requirements](https://agentsforhumans.devpost.com/) and [FAQ](https://agentsforhumans.devpost.com/details/faqs) when preparing submission. Current artifacts must not silently inherit old rules.

- [ ] Final two days, 13–14 September, are reserved for proof, hardening and artifacts rather than broad feature additions.
- [ ] Current eligibility, permitted prior work, track, Strands usage, repository visibility/licence and video rules are independently verified.
- [ ] Architecture diagram distinguishes control plane, actual OS runners, navigator/observer/repair/verifier boundaries and evidence store.
- [ ] README explains setup, permissions, test credentials, seeded fixture reset, supported profile and exact end-to-end command/UI steps.
- [ ] Public MIT/Apache licence choice and all reused code/dependency licences are reviewed against current event requirements; no unrelated private code is published.
- [ ] Video is within the current permitted duration, shows real actual-reader before/after flow and matches the recorded build; no mocked or edited false success.
- [ ] Baseline/candidate evidence bundle, known limitations and blocked R1 features are accessible to evaluators without leaking secrets.
- [ ] A fresh setup/demo rehearsal succeeded with a safe upload buffer before the deadline.
- [ ] Exact submission payload, publication choices and any terms are shown to the user for authorization immediately before external submission.
- [ ] Submission receipt/status is verified after any authorized send. Until then, report “prepared”, not “submitted”.

Creating this specification did not publish a repository, deploy resources, accept terms or submit an entry.

## 10. Product validation beyond the event

- [ ] Three target operators' actual current workflows observed or explicitly still pending.
- [ ] Two real barriers independently reproduced, including at least one externally reported incident in an explicitly authorized environment; injected examples are not customer-demand evidence.
- [ ] One repair has an actual consented human review by a recruited disabled tester/practitioner within the stated scope, or this remains explicitly unvalidated.
- [ ] Conventional-tool comparison reports setup cost, reliability, repair time and operator response; negative/neutral results preserved.
- [ ] Written design-partner/access commitment for the required authorized workflow is recorded separately; an unpaid partner establishes access, not willingness to pay.
- [ ] Actual paid pilot or explicit commercial commitment with decision-maker, scope and commercial terms exists, or willingness to pay remains explicitly unproven.
- [ ] No competitor-gap, market-size or distribution claim exceeds supporting evidence.

An engineering release can pass while commercial validation remains pending. Do not combine these into a misleading single “validated” label.

## 11. Final decision record

Select one and justify it with evidence:

- [ ] **E0 READY:** the narrow actual working slice and its integrity gates pass; R1 exclusions are listed.
- [ ] **R1 READY:** full web-product scope and all applicable platform/operational gates pass.
- [ ] **CONDITIONAL / NOT RELEASED:** useful work exists, but named external/platform/product evidence is still blocked.
- [ ] **DO NOT RELEASE:** known authority, evidence, functional, privacy or critical usability defect remains.

Decision rationale: PENDING. Failed/blocked test IDs: PENDING. Required next actions: PENDING. Reviewer identity/time: PENDING. Actual external publication/submission status: NOT PERFORMED BY THIS DOCUMENTATION TASK.
