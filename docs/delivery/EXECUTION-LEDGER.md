# AccessForge execution ledger

## Current owner override — 2026-09-16

The owner chose Codex OAuth, then explicitly retired Bedrock/AWS as the product target.
The provider-choice blocker is resolved. Finish the migration in
`docs/handoffs/codex-migration.md`, starting with Codex diagnosis/repair and then the navigator's
model, consent and observed-runtime contracts. Do not resume AWS calls, ask the owner to choose
a provider again, or treat original AWS/Strands implementation instructions as the active target.
Full actual-reader/repair/rerun/review/release scope remains. Host permissions are a separate
acceptance prerequisite, not a reason to stop this independently authorized migration.

Current C1 implementation slice (`feat/native-reference-preparation`): optional concrete baseline
fixture/browser preparation is composed inside the original reader-startup desktop claim. It
requires a fresh live build measurement and separate setup authorization; all physical preflight
gates remain. TypeScript and 24 focused synthetic orchestration checks pass. This is not an actual
host run. The private operator provisioner, stale-input-source measurement and physical acceptance
remain unfinished; do not substitute setup history for fresh negative/unknown runtime evidence.

## Current continuation queue — refreshed 2026-09-16

Verified remote/local main is `eac7d55c852bab7eb94794eeea8cc9be9188606a`: #186
(Codex diagnosis/repair and Bedrock retirement), #187 (one-action Codex planner), and #188
(Codex consent/runtime observation, database admission and finalizer binding) are merged after
exact-head CI and normal review. Do not rebuild those slices or treat their historical pending
notes as current.

Work in flight, not yet merged at this checkpoint:
1. #189 head `27d165c4798030b77279320ce3f6e5de7189648f`: provider-independent
   submission/results/limits, lazy historical adapter imports, Strands development-only packaging,
   updated Codex version matrix and platform CI reporting. Latest Python integration CI is running.
2. #190 head `68b4549969b271c1909c0fe5dc688d056935fa24`: closed Codex consent UI
   validation and matching API disclosure; 13 component checks and focused backend checks pass.
3. #191 head `f52e4233d7cbdd2e2e389f57ec47ce513af9e04d`: real disposable PostgreSQL
   and local S3 retention → finalizer → export integration passes with two synthetic CLI/desktop
   turns. Exact MODEL identity is retained but overall outcome remains INCONCLUSIVE. This closes
   that backend proof gap, not physical qualification. Temporary DBs/roles and empty buckets removed.

Next implementation/acceptance sequence:
- Finish exact-head CI/review/ordered merges for #189–191 and synchronize local main.
- Trace C1 from `apps/desktop-runner/src/execution-bootstrap.ts` and
  `native-start-listener.ts` through the actual private host/runtime callbacks. Do not add more
  provider adapters or re-ask the resolved provider selection.
- Qualify the explicitly authorized dedicated VoiceOver host (E1), then execute the original
  baseline → Codex diagnosis → constrained repair → independent rerun → human review/export
  path (E2/E3). No synthetic trace or CLI smoke may populate VERIFIED_MATRICES.
- Preserve R1 real GitHub App delivery, R2 non-AWS deployment/identity/recovery, R3 real Windows/NVDA,
  R4 fault/quality/pilot evidence and D1 release/demo/license/submission as outstanding full scope.
  Host permissions, live migrations and deployments still need their specific acceptance authority.

This is the single continuation queue, not a release certificate. Earlier snapshots below and
other PLAN/REMAINING handoffs are historical, not current task selectors. Refresh GitHub heads
before acting. No overall completion percentage has been established.

Latest operator state: local sign-in works, VoiceOver AppleScript control is ON, AWS is declined.
Runner preference reads still return EPERM. System Settings confirms the exact runner Node's Full
Disk Access entry exists but its switch is OFF; presence in Files & Folders was not a grant.
Enabling that broader permission is awaiting a new action-time confirmation, not the previous
AppleScript-control confirmation. No reader was started. See the final local-recovery checkpoint;
older host, AWS and checkout notes below are historical, not current prerequisites.

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

Follow-up read-only diagnosis: the actual Node executable was resolved via `process.execPath` and
matched to its Full Disk Access entry using Show in Finder; its host-local path is not shipped.
Its direct file read also returns EPERM, ruling out a defaults-only read failure. The matching
Full Disk Access switch is OFF. No additional permission was granted. Source selection now
distinguishes absent metadata from inaccessible metadata: only ENOENT permits looking at the legacy
path. An inaccessible or non-regular current file cannot select a stale legacy TRUE. The focused
checks remain synthetic/source-selection evidence, not actual-reader qualification.

## Baseline setup ownership checkpoint — 2026-09-15

PR #175 passed exact-head CI and merged as `e416caef93267ee07dbf4ce2f316286e3426a7ca`;
local main was synchronized and clean. Provider selection and additional preference-access
confirmation remain pending; neither prevents this C1 source correction.

The trusted Safari setup composition previously sent its authenticated fixture-reconciliation
POST before checking cancellation or the existing desktop claim. It now checks both before the
first setup effect, retaining the launcher's separate fresh authorization and native observation
gates. A focused regression reproduced the cancelled-request dispatch against the old code.
No fixture, browser, reader or OS setting was changed to exercise this synthetic test.
C1 still needs concrete runtime/reset/stale-input/action provisioning and actual qualification;
C2 window collection/authenticated finalization and the rest of the queue remain incomplete.

## Candidate reader lifecycle checkpoint — 2026-09-15

PR #176 merged after exact-head CI as `99620c3cb25896f9b97ac5e0d8e8c2d39afaa321`;
local main was synchronized before this slice. Candidate SDK startup and implicit cleanup now
have a finite asynchronous observation deadline (30 seconds by default, never above 30 seconds).
Unsettled startup returns INTERRUPTED without racing a STOP against the still-running startup;
late completion cannot enter the action loop. Cleanup timeout also remains INTERRUPTED. The
existing candidate host retains its desktop exclusion on that status: no SDK cancellation,
physical STOP, successful trace or retry permission is inferred from a timeout.

TypeScript compilation and 21 focused synthetic candidate-runner checks pass, including pending
startup, late resolution/no input and hanging cleanup. This bounds SDK promise observation only,
not synchronous SDK blocking or the complete host workflow. No actual reader or permission change.
Concrete C1 runtime provisioning and C2 measurement/finalization remain the next open boundaries.

## Startup authorization deadline — 2026-09-15

PR #177 merged after exact-head CI as `bc1192a4cbf0be30111c7e58c76e1d00bb5777a6`.
The candidate startup authorization callback now shares the bounded lifecycle observation
deadline. An unresolved callback returns INTERRUPTED before SDK startup; its late approval cannot
resume startup, dispatch input or reuse the attempt. No cleanup is invented for an SDK that was
never called. TypeScript compilation and 22 focused synthetic candidate-runner checks pass.
This is a startup control-path correction, not concrete runtime provisioning or actual acceptance.
Provider selection and preference-access confirmation remain pending; C1/C2 are not complete.

## Protected reference execution-window collector — 2026-09-15

PR #178 passed exact-head CI and merged as `faefdc0f7bf7749e8a29e8482c86cfe9cd94a04b`.
The current C2 slice adds a concrete independent database collector, not another final-row-count
predicate. The installed trigger holds a shared transaction barrier through commit. Separate
observer start/end barriers drain writers around fresh protected-history snapshots; the interval
therefore includes committed create/delete effects and excludes commits after the end barrier.
Only a pristine reserved fixture is accepted. No counter subtraction, reset or source write grant.

Twenty-five focused real isolated PostgreSQL checks passed, with Ruff and strict mypy. Scope is
one protected reference installation/fixture, not external sinks. No live database, reader, OS
permission or model change. Old installations without the barrier are refused, not auto-upgraded.
See `reference-effect-collector.md` under `docs/handoffs/` for semantics and deployment limits.

Next C2 boundary: compose collector startup before dispatch and closure after independently
confirmed STOP; authenticate/retain original records and bind finalization to the supervisor-owned
window. No production caller/finalizer uses the new collector yet. C1, actual qualification,
provider selection and all other uncompleted rows remain open; this is not project completion.

Integration review found that a per-run nonce cannot be frozen into a reusable journey assertion.
The same collector slice now separates a fixed reference policy digest from the independently
resolved concrete run/attempt/installation/nonce binding. Observer-side rule composition validates
both without rewriting the assertion or broadening the sink. Thirty-eight focused domain checks
pass alongside the 25 isolated PostgreSQL checks. This resolves policy/resource matching only;
authenticated provisioning/retention and production execution/finalizer callers remain required.

## Independent collector lifecycle admission — 2026-09-15

PR #179 passed exact-head CI and merged as `c921e75db4c5d5a4789c15d8295dc6dc2d38c75f`;
local main was synchronized. The orchestrator now owns collector begin/finish and admits
READY/CLOSED through the existing
authenticated OBSERVER stream. Startup rechecks the frozen reference policy, observer context
and zero action intents before its READY commit. Closure requires original successful STOP,
matching concrete coverage and an unchanged context, then links back to READY. Neither record
replaces the ordinary final task-completion measurement or closes its stream.

Eight synthetic lifecycle checks and two real isolated API/PostgreSQL control-plane checks pass;
Ruff and strict mypy pass. The latter uses synthetic collector values/STOP, not actual reader
acceptance. No live migration, OS setting or provider call. See `reference-effect-observer-records.md`
under `docs/handoffs/`. Independent-process/native startup wiring, original artifact retention proof
and finalizer consumption are still pending; C2 and the full project are not complete.

## Independent collector process entrypoint — 2026-09-15

PR #180 passed exact-head CI and merged as `e8f346065b1560c5c9a38b27176d369c540821d3`;
local main was synchronized. The new explicit private-pipe worker owns observer begin/finish and
emits only committed event-ID
receipts. It requires the original attempt, bounded closed input, matching READY for FINISH and
the service's independent STOP check. Signal/deadline failure aborts rather than claiming closure;
the future host must require a matching CLOSED receipt plus clean exit and own hard termination.

Twenty-seven focused worker/service checks passed, including real pipe child processes with a
synthetic observer under timeout and SIGTERM; Ruff and strict mypy pass. No live database, model,
reader or permission action. See `reference-effect-worker-process.md` under `docs/handoffs/`.
Native-process startup/liveness/closure wiring, original artifact proof and finalizer consumption
remain the next boundaries; the entrypoint alone does not complete C2 or the full project.

Native wiring follow-up: the private host now accepts an explicit independent effect observer
matching its ordinary observer's source/credential configuration. It starts exactly once after
machine-session opening, awaits READY before reader flow, fences the composed execution on worker
or receipt-pipe failure, and requires CLOSED, complete pipe framing after the original STOP and
clean worker exit before the ordinary final sample.
Existing consent, physical authorization, profile eligibility and model-call gates remain intact.
Twenty native process/observer checks pass with real children and synthetic receipts; TypeScript
compiles. No actual reader/source execution. Original artifact proof and finalizer interpretation
of the ordered lifecycle records remain pending; no C2 or project-complete claim.

## Retained reference-effect verdict wiring — 2026-09-15

The independent observer now authors versioned forbidden-effect conditions from its protected
collector, configured source installation and controller-reserved fixture. Evaluator 1.12.0
consumes them only through the existing verified original-artifact path. It joins the original
READY, every settled action pair, the successful final STOP, CLOSED and final observer sample
by canonical ordering and matching source/fixture/assertion identities. Historical records are
not upgraded; missing conditions stay UNKNOWN. The evaluator does not turn a count into an
observer-authored condition or rewrite the frozen policy to match source JSON.

Normal source review covered startup/closure authority, retained producer provenance, wrong
scope/clock/fixture, missing and duplicate conditions, early/failed STOP and historical behavior.
66 focused service/worker/retained-join checks and one isolated real API/PostgreSQL sequencer
check passed; Ruff and strict mypy passed. The isolated test database and role were removed.
No actual reader, model call, live migration, deployment or permission change occurred.

This completes code wiring, not C2 acceptance: combined original retained-artifact/finalization
proof with the actual collector/native execution still remains. C1 operator provisioning,
real VoiceOver qualification, the non-AWS provider choice/integration, baseline/repair/rerun,
human review, outbound GitHub publication and release acceptance remain open.

Combined retained proof follow-up: PR #181 passed exact-head CI and merged as
`9692838a55fd0f4f3d983345c65c0f51e6452c9d`. Three new integration cases now exercise the actual
protected collector and separate database roles through observer admission, closed canonical
streams, retained S3 bytes, finalizer, API readback, immutable replay and evidence export.
No committed insert and rolled-back insert yield TRUE for the forbidden-effect assertion;
a committed create/delete yields FALSE despite the final table being empty. Original READY/CLOSED
event references and observer provenance survive finalization. All three cases passed locally;
Ruff and strict mypy passed. Their source databases/roles and isolated control-plane test database
were removed after the run. The desktop and journal remain synthetic: missing actual build,
reader and model identities correctly keep the overall result INCONCLUSIVE. This closes the
combined backend retention proof gap, not actual native execution or full project acceptance.

## Current continuation boundary — 2026-09-15

PR #182 passed exact-head CI and merged as `5b31fc5d01efa7da68eca8dd0579478e8173cbfb`;
the primary SSD checkout's local main was synchronized cleanly. C2 backend collector/retention/
finalizer code and combined database/object-store proof are now merged, not pending implementation.
Actual private native execution, qualified reader/model evidence and the full baseline/repair/
rerun/review/release path remain unproven. Model audit confirmed Bedrock-specific sealed profiles,
consent and runtime request hooks across navigator/diagnosis/repair; a non-AWS provider/model and
spend limit are still awaiting the owner's selection. Do not replace those bindings with generic
JSON or select Qwen implicitly.

R1 prerequisite found against current official GitHub docs: the existing access probe rejected
stateless installation tokens containing dots/dashes. The bounded opaque-bearer fix now preserves
scope checks and revocation; 58 focused synthetic HTTP checks, Ruff and strict mypy pass. No actual
token issuance/publication occurred. Outbound approved-preview dispatch and durable receipt/
unknown-outcome reconciliation are still unfinished; this compatibility fix does not complete R1.

## Approved GitHub publication composition — 2026-09-15

Prerequisite PR #183 passed exact-head CI and merged as `a91c17d69e6ad7162e4527edf2128abe22459c36`.
Publication composition is PR #184, pending exact-head CI and merge at this checkpoint.
This R1 slice now composes current local authority, exact remote scope/commit, retained bytes,
the original approval and a NEW durable reservation before a single check-create POST. A matched
response and successful token cleanup are required before immutable receipt persistence. Existing
reservation recovery now exposes historical `original_creation` without claiming current remote
state or retry authority. The public read-only access probe has not gained a write argument.

67 focused unit/protocol checks and 14 isolated integration checks passed; Ruff and strict mypy
passed. HTTP was synthetic. Migration 0069 was applied only in the generated test DB, which was
removed with its role. No live migration, App token issuance, check publication, reader or model
call occurred. See `github-approved-publication.md` under `docs/handoffs/` for authority, retention,
race/unknown boundaries and remaining real App/operator acceptance. R1 is not fully accepted;
the core C1/E1/E2/E3 path and remaining R2/R3/R4/D1 deliverables remain open.

The additional completed-run cases use real retained S3 bytes and the original immutable
INCONCLUSIVE evaluation: retained evidence permits the synthetic check, deleted object bytes
prevent any create/reservation. They run before the separate diagnosis fixture intentionally
deletes retention metadata. The generated OpenAPI description was refreshed after CI identified
the stale contract; no runtime permission or retention guard was weakened.

CI at `131c3f1` subsequently ran 1,380 integration cases: 1,365 passed and 15 failed because
the explicitly pinned forward-migration boundary still named 0068. The drill now names 0069,
keeps each historical migration chain explicit, and proves a pre-existing irreversible intent
survives unchanged without inventing a creation receipt. The focused 39-case migration suite
passed; the strengthened intent-preservation case also passed separately. Only generated
disposable databases were migrated and removed by the fixture. New-head CI remains required.

## Explicit GitHub operator workflow — 2026-09-16

PR #185 depends on #184 and adds trusted-host commands for repository connection and separately
approved publication. Both require explicit remote-operation flags, closed private scope files,
private RSA key loading, current owner/session checks before key access, and the existing live
scope/one-shot publisher services. Connection never grants publication approval. Twenty-three
focused tests pass with synthetic database/signing/service ports; Ruff and strict mypy pass.
These are usable invocation paths, not proof of an isolated deployment or actual App delivery.
No real credential was loaded, token issued, check created, reader started or live DB migrated.
PR #184 passed all applicable checks at `20794c7822c49dd5c956641e410c5ef56823c5f7`
and merged as `705572af4f3d26940441b117644636a7bf79a36f`; local main is synchronized.
PR #185 is being retargeted onto that main and still requires its own exact-head CI/merge.
See the operator handoff for configuration, authority and unconfirmed-outcome boundaries.
