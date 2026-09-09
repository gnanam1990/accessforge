# AccessForge — End-to-end Implementation Plan

Version 1.0 • 9 September 2026 • Proposed execution plan, not completed implementation.

## 1. What this plan delivers

Implement the full R1 web product described in [PRD.md](PRD.md), using the immutable authority/state contracts in [CONTRACTS.md](CONTRACTS.md), technical design in [TDD.md](TDD.md), interaction design in [UI-UX.md](UI-UX.md), and the 80 scenarios in [TEST-PLAN.md](TEST-PLAN.md).

Keep **E0 event proof** separate from **R1 product completion**. E0 is one genuine authorized form-recovery journey using actual VoiceOver, real Strands orchestration, isolated patching, independent verification, human review and verifiable export. R1 adds complete multi-workspace operations and the actual Windows/NVDA profile. Mobile/PDF/native applications are R2+ and must not appear as implemented features.

For owner-authorized continuous delivery, [MASTER-BUILD-AND-MERGE.md](MASTER-BUILD-AND-MERGE.md) adds branch, commit, CI, independent PR review, protected merge and post-merge verification gates around this dependency plan. It does not alter the product acceptance requirements or make unavailable runtime proof optional.

The current event deadline used for planning is **15 September 2026, 00:00 UTC / 05:30 IST**. Recheck [official dates](https://agentsforhumans.devpost.com/details/dates) before external submission. Reserve **13–14 September** for hardening and submission artifacts; never assume the end date provides an extra day. This plan does not promise that the full product, or even E0 before capability checks, can fit the remaining calendar window.

## 2. Implementation checkout and specification precedence

This folder is a specification package. It is not the product checkout, an installed service or a claim that AWS/desktop resources are ready.

Prompt 00 selects the actual implementation checkout, inspects repository instructions and dirty state, and records existing code/licences/integrations. Do not assume CapitalDesk, FlowOps or another prior project is an authorized code donor. Any reuse requires inspecting its actual checkout and preserving its unrelated work.

Copy this complete package unchanged to `specs/accessforge/` in the selected checkout, or provide the absolute specification path. Preserve relative links. Implementation evidence goes to `docs/handoffs/NN.md` in that checkout; do not put handoffs into this source specification folder.

User/repository instructions apply first. PRD owns product intent, CONTRACTS owns exact scope/IDs/wire/state semantics, TDD owns implementation and TEST-PLAN owns proof. Any contradiction blocks the affected slice until coordinated changes resolve it. Module prompts cannot silently override a protected invariant.

## 3. Dependency gates

```text
00 Capability and authorized environment gate
  → 01 foundation → 02 contracts → 03 identity → 04 journal
  → 05 immutable projects/builds → 06 journeys → 07 runner control
                                      ├→ 08 VoiceOver → 10 evidence → 11 verifier
                                      └→ 09 NVDA [required R1; independent profile]
  → 12 Strands navigator → 13 diagnosis → 14 sandbox → 15 matched verification
  → 16 human review → 17 export → 18 API/clients
                                  ├→ 19 events/schedules → 22 setup UI
                                  ├→ 20 GitHub [required R1; opt-in publication]
                                  └→ 21 UI foundation → 23 replay / 24 review UI
  → 25 adversarial lab → 26 security/privacy/operations
  → 27 deploy/restore → 28 actual release proof/pilot → 29 independent review
```

The compact flow above is explanatory; the exact dependency table below governs scheduling. UI prototypes can be explored earlier, but a module is not accepted until its real dependency contracts work. An early static UI is a design artifact, not product completion.

## 4. Thirty separate implementation prompts

Run [prompts/SESSION-HEADER.md](prompts/SESSION-HEADER.md) before each numbered prompt. “Dependencies” means completed handoffs with evidence, not merely generated files. Parallel work is safe only after shared contract gates and with disjoint ownership.

| Module and prompt | Dependencies | Concrete deliverable | Primary proof gate |
|---|---|---|---|
| [00 — Capability gate](prompts/00-capability-gate.md) | None | Chosen checkout, authorized staging/local target, actual VoiceOver feasibility, model access, version/access/license inventory and explicit go/no-go. | T-001, T-010, T-011; no assumed runner/model readiness. |
| [01 — Workspace foundation](prompts/01-workspace-foundation.md) | 00 | Proposed Python/TypeScript monorepo, pinned tested tools, lockfiles, lint/type/test entry points, documented local dependencies. | T-073; clean setup not dependent on hidden caches. |
| [02 — Contracts and reducers](prompts/02-contracts-and-reducers.md) | 01 | JSON Schema source of truth, generated bindings, canonical hashes, pure state reducers, typed errors and independent fixtures. | T-004–T-007, T-033, T-056. |
| [03 — Identity and tenancy](prompts/03-identity-and-tenancy.md) | 02 | Session/service identity, workspace membership, role policy, consent/authorization roots, denial audit. | T-001–T-004, T-053; actual denial boundaries. |
| [04 — Journal and outbox](prompts/04-journal-and-outbox.md) | 02, 03 | PostgreSQL migrations/repositories, durable operations, idempotency, transactional outbox and worker claiming. | T-017, T-020, T-023, T-055, T-069. |
| [05 — Projects and build manifests](prompts/05-projects-and-build-manifests.md) | 03, 04 | Authorized origins/repos, immutable snapshots/manifests and real local reference application with backend/seed/reset support. | T-001, T-005, T-009; no fake completion endpoint. |
| [06 — Journey DSL and assertions](prompts/06-journey-dsl-and-assertions.md) | 02, 05 | Draft/frozen journeys, safe fixtures, allowed effects, assertion schema and independent completion observer contract. | T-007–T-010, T-030, T-032. |
| [07 — Runner control plane](prompts/07-runner-control-plane.md) | 04, 06 | Enrollment, profiles, preflight admission, desktop leases/fencing, local durable action journal, cancellation request/current-epoch stop acknowledgment, ambiguous interruption/quarantine. | T-014–T-021; actual process races and journal-before-action. |
| [08 — macOS VoiceOver runner](prompts/08-macos-voiceover-runner.md) | 00, 07 | Actual pinned VoiceOver/browser adapter, permissions/bootstrap, reset and evidence capture. | T-011, T-013–T-018, T-022; actual A-level proof. |
| [09 — Windows NVDA runner](prompts/09-windows-nvda-runner.md) | 00, 07 | Independent actual NVDA/browser adapter and supported profile; required for R1. | T-012–T-016, T-022; no borrowed VoiceOver proof. |
| [10 — Evidence ingestion](prompts/10-evidence-ingestion.md) | 04, 07, 08 | Single trusted canonical sequencer, authenticated producer/type ACLs, source-stream sequences/closing watermarks, ordered/hash-bound events, conflicts/gaps/tails and artifact completeness. Supports 09 when available. | T-023–T-028; real storage faults and supervisor-forged observer denial. |
| [11 — Outcome verifier](prompts/11-outcome-verifier.md) | 06, 10 | Independent deterministic evaluator with strict incomplete/false/unknown/completion ordering. | T-008, T-029–T-033; independent truth table. |
| [12 — Strands navigator](prompts/12-strands-navigator.md) | 07, 08, 11 | Actual Strands role execution, reader-only observations, external allowlists and bounded actions/resources. | T-015, T-016, T-035–T-038, T-044. |
| [13 — Evidence diagnosis](prompts/13-evidence-diagnosis.md) | 11, 12 | Source-linked findings and diagnosis with fact/hypothesis separation and reproducibility prerequisites. | T-029, T-039, T-043, T-054. |
| [14 — Patch sandbox](prompts/14-patch-sandbox.md) | 05, 13 | Constrained diffs, exact patch approval, protected-file enforcement and isolated untrusted source build. | T-040–T-046, T-048; real sandbox negatives. |
| [15 — Candidate verification](prompts/15-candidate-verification.md) | 11, 14 | Matched baseline/candidate identity, reset/rerun, functional and protected security regressions, verification lifecycle. | T-047–T-051; actual before/after reader path. |
| [16 — Human review](prompts/16-human-review.md) | 03, 15 | Scoped attributable reviews, independent-review policy, append-only corrections and finding resolution. | T-050, T-052–T-054, T-078. |
| [17 — Evidence export](prompts/17-evidence-export.md) | 10, 11, 16 | Private raw/redacted bundles, accessible summary and independently runnable offline verifier. | T-026, T-034, T-054, T-063. |
| [18 — HTTP API and clients](prompts/18-http-api-and-clients.md) | 05, 06, 07, 15, 16, 17 | Versioned validated API, generated SDK/CLI, stable errors, authorization/revision/idempotency semantics. | T-002, T-055, T-056; API-level contract/negative tests. |
| [19 — Events and schedules](prompts/19-events-and-schedules.md) | 04, 18 | Replayable authorized SSE and bounded opt-in ExecutionGrants minting fresh exact RUN_EFFECTS children; parent scope/revision/revocation checks at mint and dispatch. E0 may remain manual-trigger-only with disclosure. | T-057, T-058; durable events are not optional E0; grants never patch/publish. |
| [20 — GitHub integration](prompts/20-github-integration.md) | 15, 16, 18 | Explicitly authorized installation/read scope, authenticated webhooks and exact approved check/comment/PR publication. | T-045, T-059, T-060; optional E0, required R1 capability. |
| [21 — UI foundation](prompts/21-ui-foundation.md) | 18 | Accessible design tokens/components, routes, auth/error states, typed API and keyboard/screen-reader primitives. | T-066, T-067; not static visual-only approval. |
| [22 — UI projects, journeys and runners](prompts/22-ui-projects-journeys-runners.md) | 19, 21 | Working project setup, authorized environment, versioned journey authoring and actual runner readiness. | T-065, T-066, T-068. |
| [23 — UI run replay](prompts/23-ui-run-replay.md) | 10, 11, 19, 21 | Live trace, ordered reader transcript, evidence gaps, cancellation and completed/failed/inconclusive views. | T-021, T-057, T-065, T-066, T-068. |
| [24 — UI repair and review](prompts/24-ui-repair-and-review.md) | 15, 16, 17, 21 | Accessible findings/diff/approval, matched verification, separate human review and truthful export. | T-050, T-052, T-065–T-068. |
| [25 — Fault lab and benchmarks](prompts/25-fault-lab-and-benchmarks.md) | 12, 15, 22, 23, 24; also 09 for R1 | Controlled production-path faults, held-out corpus, boundary mutations and measured desktop capacity. | T-018–T-022, T-032, T-074–T-076. |
| [26 — Security, privacy and operations](prompts/26-security-privacy-and-operations.md) | 17, 19, 25 | Hardened authority/data boundaries, retention/deletion, incident/quarantine, telemetry, resource budgets and manual entitlements/usage. | T-002, T-036–T-041, T-061–T-064, T-072, T-078. |
| [27 — CI, deployment and recovery](prompts/27-ci-deployment-and-recovery.md) | 26 | Reproducible CI/deployment, supported desktop provisioning, migration/backup/restore, rollback and readiness runbooks. | T-069–T-073; executed clean-install/restore proof. |
| [28 — Release proof and pilot](prompts/28-release-proof-and-pilot.md) | 27; also 09 and 20 for full R1 | Exact-version working proof, limitations, customer comparison/pilot gates, docs/diagram/video and release record. | T-065, T-073, T-076–T-079; no fake customer/AT evidence. |
| [29 — Independent final review](prompts/29-independent-final-review.md) | 28 | Independent adversarial reproduction, finite blocker list and honest scoped release decision. | T-075, T-079, T-080; no author-only assurance. |

## 5. Milestone acceptance

### G0 — Capability and authority gate

Do not commit to the event slice before actual reader access is proven. The gate needs an authorized application, usable signed-in desktop with required permissions, pinned reader/browser feasibility, real provider/model access, safe test credentials and a resettable fixture. Record costs/approval requirements without silently creating cloud resources or enabling billed services.

If actual VoiceOver cannot run, do not replace it with Playwright and retain the screen-reader claim. Diagnose approved alternatives; if no supported actual profile can be established, E0 is blocked and the product thesis needs an explicit scope decision.

### G1 — Authoritative foundation

Complete 01–07. Freeze cross-language contracts before separate consumers diverge. Prove identity/scoping, manifest hashing, immutable reducers, outbox/idempotency and single-session admission. A working schema without PostgreSQL concurrency proof is incomplete. The reference application must contain a real backend and deterministic synthetic reset, not a static success page.

### G2 — Genuine observed baseline

Complete 08, 10–12. Establish actual VoiceOver observations, restricted navigator capability and deterministic results from complete evidence. The normal passing journey, a reproduced accessibility failure and a missing-capability/evidence condition must all be demonstrable. Start 09 independently for R1 after 07, but never block honest VoiceOver progress while pretending Windows has been proven.

### G3 — Genuine repaired candidate

Complete 13–17. A source patch is not a verified fix until independently rerun on a comparable candidate and functional/security regressions pass. Preserve a malicious-repair negative case. Human review and export retain their separate authorities. Baseline failure followed by incomplete candidate is a valid product state, not a reason to force success.

### G4 — Usable operational product

Complete 18, 19 and 21–24; include 20 for R1. UI must use real API/persisted jobs and show missing/stale/denied states. A fresh user must complete the flow without manual database edits. E0 may disable automatic schedules and GitHub publication, clearly labelled; replayable events, core cancellation, evidence and review cannot be removed.

### G5 — Adversarially exercised and recoverable

Complete 25–27. Execute—not just write—the applicable test matrix, sandbox checks, reader/UI proof, privacy deletion behavior and restore procedure. Resource budgets and manual entitlements must use durable deduplicated data. Record any unavailable platform or unsupported deployment as a blocker to its claim.

### G6 — Honest release and customer gate

Complete 28–29 with [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md). Bind documentation/video to the exact release build and profile. Event submission and external publication remain separately authorized actions. Product completion does not establish customer demand; document actual operator comparison and pilot commitment status independently.

## 6. Calendar: event slice without shrinking the product vision

The following is a **conditional event triage calendar**, not a duration estimate for all thirty modules or the full R1 product. Existing verified foundations may be reused only after auditing; otherwise build order and safety gates take precedence over the date.

| Date, IST | E0 priority | Stop/go condition |
|---|---|---|
| 9 September | Capability/authority checks; freeze the one supported profile, application and form-recovery journey. | If real AT access or permitted test environment is missing, disclose the blocker immediately. |
| 10–11 September | Integrate durable baseline evidence, actual Strands restricted navigation, constrained patch and independent rerun. | No claim of verified repair without complete baseline/candidate proof. |
| 12 September | Complete working core UI, human review/export; run highest-risk crash/denial/malicious-repair cases; feature freeze. | If the critical path needs hidden edits/mocks, it is not a real E0 product. |
| 13–14 September | Hardening, clean setup, documentation, architecture diagram, source/license disclosures, real video and submission rehearsal. | Preserve two days for artifacts; do not add broad new capabilities here. |
| Before 15 September, 05:30 IST | Final official-rules check and user-authorized submission with a safe upload buffer. | Submit only truthful supported scope; do not claim submission occurred without confirmed receipt. |

If the integration gates exceed this calendar, continue building the product and explicitly decide whether a truthful narrower event entry is possible. Do not compress security/evidence boundaries or fabricate a demo to meet it.

For R1, estimate remaining work only after G0/G2/G3 reveal actual reader reliability, patch-sandbox complexity and verification cost. Use milestone acceptance and measured burn-down, not a speculative “full product in N weeks” promise. Windows automation, privacy review and customer permissions are material dependencies, not polish tasks.

## 7. Safe parallelism and integration discipline

- After 02, schema consumers use generated bindings and common fixtures; no independently invented status names or digest algorithms.
- After 07, VoiceOver and NVDA adapters can proceed separately, each with its own real profile evidence.
- After 18/21, setup, replay and review UI modules may proceed with disjoint routes/components and shared typed state adapters.
- Source patching and outcome verification must remain independently owned by capability, even if one implementation process creates both codebases. Shared convenience access cannot collapse the authority boundary.
- Integrate one coherent vertical slice at each gate, including unhappy paths. Do not leave every real integration until module 28.
- Database/schema migration changes need consumer review and backward/recovery tests before merge. Keep unfinished feature flags explicit and disabled.

## 8. Handoff contract for every module

Every `docs/handoffs/NN.md` must contain:

1. Scope, release target (E0/R1), exact commit and dirty-state evidence.
2. Preconditions/dependency handoffs inspected and any unresolved conflict.
3. Changed paths and why the change belongs to this module.
4. FR/INV/tests covered; red test, implementation and regression proof.
5. Actual commands, result counts and failing/blocked/skipped distinctions.
6. Actual platform/model/build/database evidence versus synthetic fixtures.
7. Security, consent, privacy, resource/cost and external-action changes.
8. Immutable manifest/proof bundle identifiers and safe storage location.
9. Known limitations, defects and next eligible module.

Never paste secrets into a handoff. “Tests added” is different from “tests executed”; “API accepted” is different from “desktop action completed”; “candidate built” is different from “repair verified”; “review accepted” is different from “all users can use it.”

## 9. Definition of done

**Module done:** coherent implementation, required proof at its actual boundary, documented limitations and an auditable handoff. A stub, mock-only external integration or broken dependent consumer is not done.

**E0 done:** its entire real authorized workflow and core adverse cases pass at the named VoiceOver profile; setup, export and artifacts are reproducible; excluded R1 features are clearly listed. No public-cloud arbitrary-code-hosting claim.

**R1 done:** complete web product, multi-workspace controls, actual VoiceOver/NVDA supported matrix, opt-in GitHub/schedules, accessible UI/API/CLI, operational recovery, privacy/resource controls and applicable 80-test evidence with independent review. Commercial adoption remains separately measured.

**Never waive:** known unauthorized action, tenant leak, fabricated AT evidence, missing evidence labelled verified, blind repeat after ambiguous input, weakened validation/authentication/test assertions, inaccessible primary review, or silently unverifiable exports. These are release blockers regardless of deadline or visual polish.
