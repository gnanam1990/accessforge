# AccessForge execution ledger

Current planning checkpoint: 2026-09-15. This is the single continuation queue, not a release
certificate. Original requirements remain in `specs/accessforge/`. Earlier PLAN/REMAINING tables
and handoffs are historical evidence, not current task selectors. Refresh source and GitHub state
before changing a row. No overall completion percentage has been established.

## Verified checkpoint

- Local main: `82873d688b1a51a1a0d18dabef21842126286cfe`, PR #153 merged after #152.
  Both exact PR heads passed their applicable CI before merge; main CI is separate.
- PR #155: concrete independent observer process closure, head
  `6dc22e297d9256146c8bc8dcbca95c1f68c1aa4b`; Python CI pending at inspection.
- PR #156: private one-shot Python-to-native handoff, head `29149cb`, stacked on #155.
  Six synthetic-peer Unix socket tests and one unavailable-profile refusal test pass;
  these do not establish actual reader or model execution. Refresh CI before merge.
- PRs #147–151 delivered baseline dispatch/completion composition, loopback-only development
  login, submission drafts, webhook body deadline and explicit repository-removal denial.
  They did not establish actual-reader/model/end-to-end acceptance.

## Ordered queue

Code, execution evidence and user authority are separate. An approval in one row never blocks
independent implementation in another row. An implemented component stays implemented even when
its real execution is not yet proven.

| ID | Deliverable and source entry | Code remaining | Evidence remaining | External prerequisite |
| --- | --- | --- | --- | --- |
| C1 | Trusted operator host integration: `apps/desktop-runner/src/execution-bootstrap.ts`, `physical-preflight.ts`, `navigator-process.ts`; orchestrator `baseline_completion.py` | Concrete private controller configuration, transport, runtime callbacks, action authority and independent closure composed into a usable operator entrypoint | Focused composition checks; actual host run separately | None to implement; real reader/model execution needs separate consent |
| C2 | Forbidden-effect evaluation: domain `journeys/assertions.py`, orchestrator `completion_observer.py` and finalizer | Independent effect monitor with explicit coverage boundaries and frozen predicate; authenticated retention and finalizer consumption | Positive occurrence, complete absence, interrupted coverage and unavailable-monitor cases | No external authority for scoped code; any new external effect monitoring requires its own scope |
| E1 | Actual VoiceOver qualification: `packages/at-adapters/voiceover/src/profile.ts` | Preserve fail-closed matrix; fix only defects found in qualification | Original physical traces for exact supported host/profile; matrix currently empty | Reader startup and OS permission approval; dedicated desktop |
| E2 | Real baseline → diagnosis → repair → independent candidate rerun | Connect any remaining C1 integration gaps without bypassing existing approval/build/evidence boundaries | Real Strands use, failed baseline, original S3 artifacts, complete finalization, constrained patch, fresh rerun and protected regressions | E1; provider credentials and explicit spend cap; exact patch/rerun approval |
| E3 | Human review → offline export → fresh setup rehearsal | Repair concrete integration defects found in the normal operator path | Actual review, independently verified export, reload and interrupted-flow evidence | E2 and real reviewer; no invented feedback |
| R1 | GitHub App checks/publication: orchestrator `github_*` | Finish outbound publisher and unknown-outcome reconciliation; remaining installation lifecycle and credential isolation | Actual scoped App delivery/publication | App installation/key provisioning; existing repository PR work is separately authorized |
| R2 | Production identity and deployment: API config/auth, `infra/aws` | Production identity integration and deployment configuration gaps | Fresh install, tenant isolation, recovery and capacity evidence | Provider configuration; deployment/live migration approval |
| R3 | Windows/NVDA: `packages/at-adapters/nvda` | Actual adapter beyond the blocked contract | Supported Windows reader qualification and real journey | Windows host and reader permission |
| R4 | Fault laboratory, benchmarks and pilot | Complete requirement-linked fixtures and reporting after core path works | Held-out actual-reader quality, faults, real operator/pilot and final release review | Qualified hosts, model budget and participants |
| D1 | Release assets: `docs/delivery/SUBMISSION-READINESS.md` | README/diagram and narrative/video-plan drafts exist; final artifacts still need actual evidence | Working demo recording and authorized submission receipt | Owner license choice, evidence from E2/E3, publication approval |

## Immediate next implementation

Start **C1**, not another unrelated webhook enhancement. Trace each required callback from
`runProvisionedNavigatorExecution` through physical preflight and the bounded child process.
Observer closure and private dispatch transport are implemented in #155/#156, not yet merged at
this checkpoint. Finish the operator-owned runtime/action configuration and shipped entrypoint,
retaining
one-shot session ownership, original journal identity and independent observer closure. Do not
replace a missing callback with an always-allow stub or populate VERIFIED_MATRICES from versions.
Record the changed files, focused check result and exact next unfinished boundary here.

If a specific C1 step genuinely requires new authority, document that exact step and proceed to
C2's independently buildable measurement contract. A final zero application count is not proof
that no transient or external forbidden effect occurred. Missing coverage remains UNKNOWN.

## Continuation and completion rules

1. Inspect working tree and this ledger; preserve uncommitted work. Refresh open PR heads and CI.
2. Complete one coherent code slice from C1/C2 before optional polish. Waiting CI is not a reason
   to stop independent work. Do not repeatedly restart CI or run local full suites.
3. Use normal source review and focused checks. Merge only the exact checked head with applicable
   CI passing and repository requirements satisfied. Merge #155 before retargeting #156 to main;
   verify its current checks before merging. Synchronize local main after confirmed remote merge.
4. Update this file on material progress: code/commit, evidence class, precise blocker and next
   operation. A status-only cycle is not implementation progress. If all safe work is exhausted,
   identify the specific missing authority rather than silently looping.
5. Never equate scheduler ACTIVE, a stored goal status, green CI, merged code, or drafted release
   prose with product completion. Finish only when requirements and corresponding real acceptance
   evidence are complete; retain full R1 tasks rather than quietly dropping them.

Previously authorized isolated local Docker/Colima, PostgreSQL and MinIO work does not need the
same permission again. It does not authorize paid models, real AT startup, OS permission changes,
deployment, live migrations, license selection or public submission. No Claude app, subagents or
specialized PR-review skills; preserve the user's focused-validation preference.

## Continuity repair checkpoint

2026-09-15: Consolidated the fragmented continuation queue and separated buildable code from
physical/provider/release acceptance. Historical documents now point here. This change repairs
work tracking, not product code. C1 remains the next implementation, and E0/R1 remain incomplete.

The full build-flow audit is retained outside the checkout as
`/Users/kratos/Documents/Codex/AccessForge-Build-Flow-Audit-2026-09-15.md`.
It verifies all 43 original build-pack Markdown files match the checked-in requirements and
maps the 30 modules, 25 requirement families and 80 scenario groups. Its F1–F8 findings separate
missing production connections from missing actual acceptance; it is not a fresh runtime pass.
Continue G2 baseline → G3 repair/rerun/review → G4 fresh operator → G5 faults → G6 release.
