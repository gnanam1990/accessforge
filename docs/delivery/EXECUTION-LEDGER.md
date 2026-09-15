# AccessForge execution ledger

Current planning checkpoint: 2026-09-15. This is the single continuation queue, not a release
certificate. Original requirements remain in `specs/accessforge/`. Earlier PLAN/REMAINING tables
and handoffs are historical evidence, not current task selectors. Refresh source and GitHub state
before changing a row. No overall completion percentage has been established.

## Verified checkpoint

- Remote main refreshed at `59c929c041d704f2b5383e6314556841b815a5a0`: PRs #162–167
  are merged, superseding their historical pending notes below. #167 exact head
  `e4ec1dae4c4ba3ed6666256ec988c5a4579f0639` passed all applicable CI before merge.
  The original external-SSD checkout is not confirmed synchronized: file reads and Git status
  are stalled despite the volume remaining mounted. No disk reset or original checkout edits.
  Work continues in a separate internal temporary clone from this verified remote main.
- Current qualification-path slice: fresh candidate preflight before startup, after startup and
  immediately before actions; separate mandatory startup authorization; late-probe input fence.
  TypeScript build and 15 focused synthetic checks pass. See `candidate-proof-teardown.md` under
  `docs/handoffs/`. This removes a cold-reader startup deadlock, not C1/E1 completion.
- Local main: `2355e16f4b0a007ab1529d86415fa513aef029eb`, PRs #156–160 merged after #155.
  Exact PR heads passed applicable CI before merge; main CI is separate.
- PR #155 delivered concrete independent observer process closure. Its exact checked head was
  `6dc22e297d9256146c8bc8dcbca95c1f68c1aa4b`.
- PR #156 added the private one-shot Python-to-native handoff, including an actual cross-language
  socket check with test-only execution/profile substitution. #157 added real journal fsync probes;
  #158 shipped the trusted private native-host entrypoint. #159/#160 fixed cached speech and
  READ_CURRENT command handling. These merged after exact-head CI; no physical acceptance claimed.
- PR #161 (`2d4b8f1`): native PID-addressed VoiceOver last-phrase channel probe, merged after green CI.
- PR #162 (`15eeb03`): candidate cleanup before final trace closure, nine focused synthetic checks;
  pending Python CI. Partial startup now attempts cleanup; cleanup uncertainty interrupts the run.
- PR #163: C2 independent effect coverage contract, fifteen focused synthetic checks, Ruff/mypy pass.
  No collector, authenticated retention or finalizer connection yet; see
  `docs/handoffs/forbidden-effect-coverage.md`. Refresh remote heads/checks before any merge.
- Next C2 slice: frozen continuous-effect absence predicate and collector-side rule composition;
  51 focused synthetic checks, Ruff/mypy pass. Source admission, actual collector and finalizer
  remain pending; see `docs/handoffs/frozen-effect-absence.md`.
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
Observer closure, private dispatch transport, journal probing and the native-host entrypoint are
merged in #155–158. Finish the operator-owned runtime/action configuration,
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
   CI passing and repository requirements satisfied. Synchronize local main after confirmed remote
   merge; distinguish temporary-checkout synchronization from the original SSD checkout.
4. Update this file on material progress: code/commit, evidence class, precise blocker and next
   operation. A status-only cycle is not implementation progress. If all safe work is exhausted,
   identify the specific missing authority rather than silently looping.
5. Never equate scheduler ACTIVE, a stored goal status, green CI, merged code, or drafted release
   prose with product completion. Finish only when requirements and corresponding real acceptance
   evidence are complete; retain full R1 tasks rather than quietly dropping them.

Previously authorized isolated local Docker/Colima, PostgreSQL and MinIO work does not need the
same permission again. The owner subsequently authorized reader startup/settings work. The specific
AppleScript-control security change is awaiting its action-time confirmation; it remains OFF.
Live preflight found Accessibility available, Automation UNKNOWN and no dedicated desktop binding.
Provider credentials were absent from the inspected process environment and project `.env` keys;
no paid call was made and no concrete provider/spend cap has been supplied. Do not infer deployment,
live migration, license selection or public submission authority. No Claude app, subagents or
specialized PR-review skills; preserve the user's focused-validation preference.

## Continuity repair checkpoint

2026-09-15: Consolidated the fragmented continuation queue and separated buildable code from
physical/provider/release acceptance. Historical documents now point here. This change repairs
work tracking, not product code. C1 remains the next implementation, and E0/R1 remain incomplete.

The full build-flow audit is retained outside the checkout as the operator-local artifact
`AccessForge-Build-Flow-Audit-2026-09-15.md` (not shipped or assumed available to other operators).
It verifies all 43 original build-pack Markdown files match the checked-in requirements and
maps the 30 modules, 25 requirement families and 80 scenario groups. Its F1–F8 findings separate
missing production connections from missing actual acceptance; it is not a fresh runtime pass.
Continue G2 baseline → G3 repair/rerun/review → G4 fresh operator → G5 faults → G6 release.

## Operator runtime checkpoint — 2026-09-15 follow-up

The original SSD checkout recovered and local main was verified clean/synchronized at
`59c929c041d704f2b5383e6314556841b815a5a0` before starting the next branch. This supersedes
any earlier unresolved local-sync note; no SSD reset was performed. API/reference-app readiness
and the web server responded. A read-only existing-fixture Safari diagnostic returned
`SAFARI_NOT_FOREGROUND`, not focus proof or reader qualification. No form was submitted, fixture
reset, reader started or permission changed. AWS configured-profile inventory remained empty.

Current controller correction: share a 1800-second maximum/default original-STOP wait between
the synchronous baseline operator and async dispatcher, retaining short delivery acknowledgement
and all independent native/lease/approval deadlines. The prior 60-second hard ceiling could cut
off a valid native execution. Forty focused synthetic checks, Ruff and strict mypy pass; see
`docs/handoffs/baseline-reader-cancellation.md`. This is not C1 completion: concrete trusted runtime,
physical focus/effect configuration and actual baseline/repair/rerun evidence remain outstanding.

## Protected effect-source checkpoint — 2026-09-15

PR #169 completed exact-head CI and merged as `9f6277901b5d1d9b6e95b17027d874fbb2014152`;
the original checkout and temporary recovery clone were synchronized before this slice.
The current C2 source component adds opt-in protected committed-insertion history for the reference
app, preserving a create-then-delete and refusing weakened audit authority. It includes transactional
installation rollback and real isolated PostgreSQL privilege/trigger checks. No live application
schema, desktop security setting, model account or production deployment was changed.
See `docs/handoffs/reference-effect-audit.md` for provisioning and evidence limits.

C2 is NOT complete: independent run-window collection, authenticated retention and canonical
finalizer integration remain. C1 concrete runtime/action configuration, E1 real reader qualification,
E2 baseline/repair/rerun and the existing remaining release queue are still open. Protected database
history and green CI are not actual VoiceOver acceptance or full-project completion.

## Qualification entrypoint checkpoint — 2026-09-15

Main is synchronized at `48cef6c0bd53f788d2ec22986e115d6b321d940a` after PRs #170–173.
The protected history reader now requires the installed independent observer identity; local
candidate trace storage pins private file identity; the explicit `--candidate-proof` host command
assembles the physical probes, journal, shared desktop claim and lazy real reader adapter.
These are implemented foundations, not completed C1/C2 or a qualified matrix.

The next source correction makes candidate runners single-use and rejects nonterminal STOP
before startup. TypeScript compilation and 43 focused synthetic/filesystem checks pass; two new
regression cases failed against the prior compiled source. No actual reader was started.

The VoiceOver Utility was inspected again: AppleScript control is OFF. The exact security-setting
confirmation is pending; do not repeat the same question every continuation. Provider/profile and
budget are still unspecified. Next C1 work remains concrete trusted runtime/reset/stale-input and
action authorization provisioning, then actual reader qualification when its prerequisites exist.
C2 still needs independently bound execution-window collection and authenticated finalization;
the existing R1–R4, E2/E3 and D1 queue is unchanged. Do not substitute helper PRs for those outcomes.

## Local operator recovery — 2026-09-15

PR #174 merged as `df3c89e5117bbc62f42b49f707f33a784f747b86` after exact-head CI. The owner
subsequently declined AWS use and confirmed the pending local-login/VoiceOver-control changes.
AWS profiles are no longer a prerequisite for the requested local workflow; no paid provider was
selected or invoked. Production identity/deployment remains separate, not waived as complete.

VoiceOver Utility now visibly shows AppleScript control enabled. Local-development login is
restricted to loopback in the ignored local configuration; browser sign-in and the existing owner
workspace dashboard were exercised successfully. No runner is enrolled in that workspace. These
facts supersede the earlier permission-pending and sign-in-unavailable checkpoints.

Live qualification exposed a real preflight defect: unreadable preferences were described as OFF,
and a static blocked reason claimed this host had never configured VoiceOver. The preference probe
now selects the current group-container location before legacy, never falls back from an unreadable
current source to a stale legacy value, and reports unavailable/malformed observations as UNKNOWN.
Only observed zero reports disabled. Static profile text now describes missing qualification proof.
The controlling process still cannot read the preference on this host; this remains UNKNOWN and
does not unlock startup. The next operation is to establish supported runner preference access,
then perform actual qualification; no actual speech capture, journey, matrix enrollment or canonical
reader evidence has been produced by this correction. C1/C2 and remaining acceptance work remain open.
