# AccessForge execution ledger

## Verified continuation checkpoint — main through PR #216

Refreshed 2026-09-16 after both login PRs merged. Verified clean local/remote main:
`704563f2942e0140c38f76656908e87c7013f378`. No PR remained open at this checkpoint.
This supersedes earlier pending-head lists below; refresh GitHub before new work.

- #215 merged as `4cbde098257effcba47fc3784a3ac0c0865f0322` after exact head
  `eb790bc37d01dddccbc4ca95d102cdb1ca36116d` passed CI run `35089591966`.
- #216 merged as `704563f2942e0140c38f76656908e87c7013f378` after exact head
  `45939574e277e147d5682cc26a37eba183a2a0c4` passed CI run `35089599794`.
  It was retargeted from the merged parent to main. Both prospective merge trees
  differed from tested source only by #217's two delivery checkpoint documents.
  Normal source review is recorded on both PRs. No post-merge CI result is implied.
- The prior strict test-typing failures and exact Retry-After route-contract
  failure were corrected before these successful runs. OAuth start really has
  a global database admission limit; ordinary GETs and callback do not acquire
  a Retry-After promise. No runtime guard was weakened to pass the contract test.

### Retained source/web release candidate

`accessforge-704563f.zip`, source commit above, SHA-256:
`24661c82278131065b6a1b1c64c21c8afc60a5d9f2cf84d7b5cd6b45d99ff61e`.
Fresh main TypeScript/Vite build passed. The 3,529,955-byte ZIP's CRC, exact member
set, all 972 inventoried file sizes/hashes and all four fresh web files matched.
An independent extracted `uv sync --frozen --no-dev` installation passed the
isolated import check: four modules, six schemas, seventy-two migration files.
No migrations were executed. This is installed-source/import evidence, not service,
OAuth, reader/model, human review or deployment acceptance. The bundle does not
contain native reader binaries, credentials, dependencies or this later checkpoint.

### Next work and unchanged acceptance boundaries

Hosted-user login routes, configuration and provider-aware UI are now **built and
merged**. Do not rebuild them or confuse GitHub user identity with Codex model OAuth.
Dedicated OAuth app configuration, trusted account binding, TLS/proxy/logging and
actual browser round-trip acceptance remain separate operator work.

C1 still needs a concrete independently observed stale-input source and live
focus/effect authority with controller credentials/consent. The provisioner's
UNKNOWN must stay UNKNOWN; a PID list or keyboard focus is not proof of safe
VoiceOver cursor activation. C2 reference-effect collector/finalizer wiring is
already built and must not be recreated from historical missing-module notes.

The owner explicitly requires **VoiceOver build/compile only, no startup/runtime
tests**. Actual baseline → diagnosis → constrained repair → independent rerun →
human review/export remains unproven, not waived or replaced by this release ZIP.
Windows qualification, real GitHub App publication/recovery, non-AWS deployment
and capacity acceptance, independent held-out corpus/oracle/pilot, and final
license/demo/submission decisions remain open. No percentage or full-completion
claim follows from the merged code. Continue safe implementation where concrete
gaps exist; do not manufacture passing host authorities or repeat full local
suites merely to keep working.

## Earlier verified checkpoint — main through PR #214 (historical)

Refreshed against source/GitHub on 2026-09-16. Remote/local main is
`ff8ac6c838ba5c19022708839ef82353c3329fc3`. This snapshot supersedes the older
#207 checkpoint below for task selection; it is not a claim of full completion.
Normal review and required exact-head CI remain merge gates. VoiceOver is
build/compile only: no startup/runtime test, OS-permission change or actual
reader/model acceptance is authorized by this continuation checkpoint.

### Delivered since #207

| PR | Delivered slice | Confirmed merge commit |
| --- | --- | --- |
| #208 | Prior delivery-flow reconciliation | `b883810408d19100f52f9fde2ad655f606c9953a` |
| #209 | Isolated missing-label fixture; protected PostgreSQL UTF-8 initialization | `948582e8b9ee5b49399dc7ab37b59f0fc8c92b4e` |
| #210/#211 | Broken focus recovery and keyboard trap variants, merged as the tested #211 composition | `ee5ccfde404a8654b5a8b9e63ce7fd6172f6f7bd` |
| #212 | Fixed-host, bounded GitHub user identity transport with S256 PKCE | `0a208cf6a825daa72de77f440fac65f994e047ff` |
| #213 | Independently committed, single-use browser-bound OAuth challenges | `bee0f540f3b328cbaf20c1e7ce08a8330d33ec8e` |
| #214 | Explicit operator account binding; audited, revocable provider-bound sessions | `ff8ac6c838ba5c19022708839ef82353c3329fc3` |

#210 was resolved through the exact tested cumulative #211 head, not by claiming
its then-pending standalone run had passed. Its standalone checks are now also
verified successful. The versioned fault variants preserve the existing backend
contract. The owned baseline still requires its originally approved `inaccessible`
variant; none of the new variants was silently substituted into a sealed run.
Fixture HTTP/database validation and emitted-JavaScript checks do not prove an
actual reader detects the defects or establish independent oracle labels.

### Pending heads — refresh rather than assume

- #215: browser start/callback/configuration and generated contract work at
  `c5b6d215a8e97bc70af6a5e4b25ec89e5d5182a3`. Original CI failed strict typing
  in two new test files; those errors were corrected and the latest run is pending.
- #216: provider-aware UI/public discovery at
  `0e8464f33bb922fbdea3b01f2f5b13bfce8dc6e0`, stacked on corrected #215; CI pending.
- Merge only the current validated head, sync local main, then rebuild the release
  artifact once that composition is settled. Do not label open-branch UI as merged.
- Optional GitHub app-user login is **not Codex model OAuth** or publication via
  the GitHub App. No dedicated OAuth app, live binding or credential configuration
  was installed. Synthetic provider identity in ASGI checks is not OAuth acceptance.

### Remaining requirements, separated by evidence needed

| Boundary | Current source/evidence | Still required |
| --- | --- | --- |
| C1 native host | One-shot private provisioner, shared deadline and live probe interfaces built | Concrete independent stale-input/focus-effect authority and operator credentials/consent; never always-allow callbacks |
| C2 effect integration | `finalize_execution.py` imports and consumes authenticated reference effect assertions | Actual configured run; protected committed insertions do not cover arbitrary external effects |
| Reader/model repair loop | Codex runtime and baseline/repair/rerun/review plumbing built; matrix explicitly does not claim actual-reader qualification | Real permitted baseline → diagnosis → constrained repair → independent rerun → human review/export; VoiceOver tests remain paused |
| Hosted identity | Transport/challenge/account-session layers on main; routes/UI pending CI | Merge current heads, then separate approved real OAuth/TLS/proxy/logging acceptance |
| Publication/deployment | GitHub App and non-AWS release infrastructure built | Real installation/publication/recovery and deployment/capacity evidence |
| Windows and benchmark acceptance | Narrow NVDA driver, fault variants and bounded benchmark accounting built | Real Windows host/qualification, independent held-out oracle/corpus, actual mutation/unsafe-comparison and pilot evidence |
| Final delivery | Source/web packaging exists | Owner license/demo/submission choices and final accepted deliverables, not a renamed source ZIP |

The latest retained ZIP `accessforge-ee5ccfd.zip` is through #211; SHA-256 freshly
rechecked as `17ae75c5d6e9d63ecf917fbbc409e1d657cfe2467c12ecec861ccf98f5d9ebe1`.
It does not contain #212–216. No fresh extracted install or physical acceptance
was performed for this checkpoint. Build tests used dedicated disposable local
PostgreSQL only; those task-owned containers were stopped and removed. No live
database migration, deployed identity change or reader execution occurred.

## Earlier verified checkpoint — through PR #207 (historical)

Source and GitHub were refreshed on 2026-09-16. Remote/local main was clean at
`a3004d622925e721a3ff45d2e2587d7f1a65beef`; no open PR remained at that checkpoint.
Refresh live Git/CI before later merges. The older checkpoints below are history,
not instructions to rebuild completed slices or restart a reader.

Owner constraints remain: Codex OAuth, no AWS/Bedrock target; build/integration
only, with actual VoiceOver startup/runtime testing paused. Normal review and
required exact-head CI remain merge gates. Do not infer physical acceptance from
synthetic checks, native compilation, a source ZIP or GitHub checks.

### Delivered since the older #199 checkpoint

| PR | Delivered slice | Confirmed merge commit |
| --- | --- | --- |
| #200 | Narrow NVDA driver; Windows native host and qualification still open | `cb2d939d54e577eb866ea559e151a2baa8d8fca3` |
| #201 | Effect collector startup cancellation | `7a20033f853d9bc3c5d7304702cfa0838573cc0d` |
| #202 | Earlier delivery-status checkpoint | `a3d561637ac9a5ff6ece5b064db5454790cfd53c` |
| #203 | Baseline physical-action authority cancellation | `361952c1c3e1128488226d8dc85f7253b4b25d01` |
| #204 | Frozen benchmark denominator accounting | `aa652d715fc5af358120965ee4615adc0f41bcc3` |
| #205 | Readable outcome/evaluator bound to signed manifest | `57ec48e48042a07f8ff15c526425073ec0e9849f` |
| #206 | Retained benchmark ZIP hash/signature inspection | `30d6a29e23e7e63a6fc3c06fdb1cccb434a2a1e3` |
| #207 | One-shot reference native-host provisioning factory | `a3004d622925e721a3ff45d2e2587d7f1a65beef` |

These PR heads passed applicable CI and normal source review before merging.
This does not assert that each separate post-merge main run has completed.

### Current core boundary and next work

**C1:** `createReferenceNativeProvisioner` now assembles a real private attempt
directory, FileJournal, shared monotonic deadline and receiver-derived lease and
navigator references. Required live artifact/reference setup and explicit
authority ports remain. The old statement that no provisioning assembly ships
is superseded. The deployment-specific operator module still needs real
stale-input observation, live focus/effect authority and controller-issued
credentials/consent. Do not replace these with always-allow callbacks. In
particular, keyboard focus is not proof of a VoiceOver-cursor activation target.
Next implementation must resolve those concrete authorities/measurements without
loosening the existing closed-world action, session and evidence boundaries.

**C2:** the protected reference-effect collector, authenticated READY/CLOSED
records, native worker ownership and finalizer join are built. Current
`finalize_execution.py` consumes `reference_effect_evidence.observed_assertions`
after retained-byte/stream checks. The join binds original READY, settled action
pairs, successful final STOP, CLOSED and the ordinary final observer sample.
See `docs/handoffs/reference-effect-observer-records.md` for backend integration
evidence. Do not rebuild the collector/finalizer because older handoffs say they
are missing. Its scope is protected committed reference insertions, not arbitrary
external effects; actual configured reader/model execution remains unaccepted.

**E1/E2/E3:** no qualified actual-reader baseline → diagnosis → constrained repair
→ independent rerun → real human review/export is established. Keep the matrix
unchanged and respect the owner's runtime-test pause. Code wiring and permission
are separate from execution evidence.

**R1/R2/R3/R4/D1:** real GitHub App installation/publication, production login and
non-AWS deployment/recovery, Windows native host/qualification, full fault corpus
and held-out actual benchmarks/pilot, final demo/license/submission remain open.
The benchmark report preserves denominators and inspects retained bytes but does
not verify cohort identity, oracle labels, independent execution or acceptance.
Codex model OAuth is not the product's hosted-user login choice.

### Available build evidence, not acceptance

The source/web ZIP at main `30d6a29e23e7e63a6fc3c06fdb1cccb434a2a1e3`
contains 955 independently size/hash-checked inventory files. Archive SHA-256:
`bd7636e46b9034ed979c307e1a279cded60e5e92f16ed8958f6b7009418c1511`.
It includes #206 but not #207. Fresh TypeScript/Vite build passed. Native Safari
origin and VoiceOver capture helpers compiled to arm64 Mach-O binaries without
being executed. These checks do not establish fresh full-runtime installation,
reader/model execution, human review or deployment. Older temporary ZIP paths
below are historical and must not be advertised as currently available.

## Historical checkpoints — superseded for task selection

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
gates remain. Preparation rechecks actual console/process session, screen lock and permissions,
not only the cooperative claim. TypeScript and 25 focused synthetic orchestration checks pass. This is not an actual
host run. The private operator provisioner, stale-input-source measurement and physical acceptance
remain unfinished; do not substitute setup history for fresh negative/unknown runtime evidence.

## Current continuation queue — refreshed 2026-09-16

Merged #199 (`feat/same-origin-web`): optional `ACCESSFORGE_WEB_DIST_DIRECTORY` serves a
bounded immutable trusted web snapshot through the API origin. SPA deep links and public assets
work without a Vite development server; API/health handlers and OpenAPI remain unchanged. Private
files, source maps, missing assets and build symlinks are not served. Eleven focused in-process
checks pass; no service process, live database, model or reader is started by those checks. This
closes a built-UI serving gap, not production identity, deployment or physical acceptance. The
owner's instruction remains build-only, with actual VoiceOver runtime tests paused.

Merged #198 (`feat/release-bundle`): the trusted release workflow now builds web assets
after required CI and uploads a downloadable source/web ZIP with checksum, exact source commit,
per-file inventory and explicit limitations. The packager refuses dirty tracked inputs, symlinks
and overwrite, and excludes untracked source secrets. This is build output, not deployment or
reader/model acceptance. The owner requested build-only work; actual VoiceOver tests remain paused.
Private operator provisioning and stale-input measurement are still open; packaging does not close
those runtime integration gaps. #197 is merged at `b98137a9957cf6e7ed343911ce33263ea9fc96b6`
after exact-head CI and normal review; local main was synchronized to that commit.
Local release rehearsal: the bundle at source commit `6165368241ca5e9df3508033d5b4ffd82900acb5`
was extracted into a new directory and installed with frozen, non-dev dependencies. All four
API/navigator/diagnosis/repair modules imported; six schemas and seventy migration files loaded.
No services were started and no migrations executed. The workflow now performs an isolated-Python
extracted-source import check before upload. This establishes install/import behavior, not the
complete fresh-install product path or actual reader/model execution.

Latest owner instruction: continue build/integration only; do not perform further actual VoiceOver
startup or runtime tests. The manual host attempt below is historical. Actual qualification stays
pending and must not hold up independently buildable code.

Merged #197 (`fix/candidate-authority-cancellation`): startup and action authorization now
receive per-operation cancellation signals, composed with host cancellation at the private operator
boundary. Timeout, refusal and completion close those signals. A late physical preflight is fenced
before opening a stale approval request, not merely before reader dispatch. SDK start/stop remain
non-cancellable and unresolved startup still retains the claim. Concrete private provisioning and
stale-input measurement are still unfinished; this is lifecycle integration, not qualification.
TypeScript compilation and 31 focused synthetic candidate/host checks pass, including cancellation
on timeout, fresh per-action signals and zero approval callbacks after late preflight completion.
No actual reader, model call, database migration or deployment was performed for this build slice.

Qualification setup follow-on (`feat/qualification-reference-preparation`): after #195 merged
as `583bc1b6c222bf98cae77c47a68b380ace47c643`, the labelled candidate host now composes the
same optional reference preparation before its first preflight, after action validation. It
uses the original candidate claim/deadline, aborts setup on caller/deadline cancellation, and
does not repeat setup during action-time probes. Fresh runtime evidence wins over setup history.
TypeScript compilation and 37 focused candidate/host/preparation checks pass, including refusal
of unapproved typing before setup authorization. These are synthetic orchestration/refusal checks,
not actual qualification. Concrete private provisioning, stale-input measurement, actual reader
qualification and the full repair/rerun/review/release acceptance remain open.

Verified remote/local main is `bac2b501efffff9078bc7ef9856fcb87473880a1`: #186–199
are merged after exact-head CI and normal review. This includes Codex diagnosis/repair,
Bedrock retirement, navigator/runtime/consent composition, provider-independent packaging,
consent UI and retained finalization/export integration. Do not rebuild those slices or
treat their historical pending notes as current. The retained integration uses synthetic
CLI/desktop observations and remains INCONCLUSIVE, not actual-reader acceptance.

Also merged: #192 native OAuth environment forwarding, #193 profile validation before startup,
#194 non-AWS operator documentation, #195–196 reference preparation integration, and #197 bounded
approval cancellation. Do not repeat these as pending work. #198 merged as
`8e60b886545510cf168351feb6e59c0e35cee9a4` and #199 as
`bac2b501efffff9078bc7ef9856fcb87473880a1` after exact-head CI and normal source review.

Current open implementation PRs, not yet merged at this checkpoint:
- #200 head `e2d55ad3b5d31f8b8e1d2c859c003cbd5b356a69`: narrow NVDA driver,
  closed command mappings and guarded lifecycle; 27 synthetic checks pass. Native Windows
  host composition and qualification remain pending.
- #201 head `ffdd4cc36ffcff3458bb2d3730c36394b718b8d5`: prevent late reader-startup
  approval from starting the independent effect collector after cancellation; 17 focused
  synthetic authority/process checks pass. No actual reader or model run.

Fresh main release rehearsal at `bac2b501efffff9078bc7ef9856fcb87473880a1`:
web compilation/build succeeded; the ZIP's 941 inventory members matched their size/hash.
Archive SHA-256: `2a4fd1f604c99231d8d7564122604a9d20b89d7d5dc8c187a1ee3c5b5c3714e7`.
The separately extracted frozen non-dev install imported four API/operator modules,
loaded six schemas and seventy migration files. No migration ran or service started.
The ZIP excludes #200/#201 because they are not on this source commit. It is a
source/web release candidate, not a full runtime or deployment acceptance artifact.

Next implementation/acceptance sequence:
- Finish exact-head CI/review/merges for #200–201 and synchronize local main.
- Trace C1 from `apps/desktop-runner/src/execution-bootstrap.ts` and
  `native-start-listener.ts` through the actual private host/runtime callbacks. Do not add more
  provider adapters or re-ask the resolved provider selection.
- Concrete C1 gap: no shipped `provisionNativeHost`/`provisionCandidateProof` implementation
  exists outside tests. Live build and speech probes exist, but stale previous-automation input
  measurement still depends on a trusted boolean callback. Do not hard-code that observation
  from a desktop lock, a keyboard-layout reading or a missing process: none proves the contract.
- Keep physical acceptance paused under the latest owner instruction. After a future explicit
  request to resume actual tests, qualify the dedicated VoiceOver host (E1), then execute the original
  baseline → Codex diagnosis → constrained repair → independent rerun → human review/export
  path (E2/E3). No synthetic trace or CLI smoke may populate VERIFIED_MATRICES.
- Preserve R1 real GitHub App delivery, R2 non-AWS deployment/identity/recovery, R3 real Windows/NVDA,
  R4 fault/quality/pilot evidence and D1 release/demo/license/submission as outstanding full scope.
  Host permissions, live migrations and deployments still need their specific acceptance authority.

This is the single continuation queue, not a release certificate. Earlier snapshots below and
other PLAN/REMAINING handoffs are historical, not current task selectors. Refresh GitHub heads
before acting. No overall completion percentage has been established.

Latest operator state — live host checks on 2026-09-16: the owner explicitly approved Full Disk
Access for ChatGPT.app after the exact Node grant alone left EPERM. Both approved entries are ON;
the older ambiguous Node entry remains OFF. A fresh runner process now reads the VoiceOver
preference successfully and reader-control configuration is TRUE. Do not repeat the resolved
FDA permission request or describe EPERM as the current blocker.

Read-only host probes report macOS/Safari profile-version checks, Accessibility and unlocked
screen TRUE. Console and runner process both report audit session 100023; that observation alone
is not a provisioned desktop assignment. Automation returns native status -600 while VoiceOver
is stopped, therefore UNKNOWN, not denied. Build/reset/origin/stale-input and other owned runtime
evidence remain unavailable without the concrete private provisioner.

With the owner's reader-startup approval, a bounded manual System Settings startup was attempted.
The VoiceOver switch briefly became ON, then reverted OFF. Native logs show VoiceOver PID 16818
starting and entering its exit handler at 12:31:32 local time; a subsequent process probe found
no running VoiceOver. The reason for exit is not established. Capture responsiveness was false;
no reader action, speech trace, model call or qualified matrix resulted. VoiceOver is confirmed
OFF after this attempt. This is historical, not permission to retry. Continue concrete private
host provisioning as build work only; startup diagnosis/runtime testing is paused by the latest
owner instruction. Do not bypass UNKNOWN checks or enroll the matrix from this host check.

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
| E2 | Real baseline → diagnosis → repair → independent candidate rerun | Connect any remaining C1 integration gaps without bypassing existing approval/build/evidence boundaries | Actual Codex OAuth execution, failed baseline, original S3-compatible artifacts, complete finalization, constrained patch, fresh rerun and protected regressions | E1; execution-host Codex login and exact model consent with acknowledged account-usage semantics (admission holds are not a spending cap); exact patch/rerun approval |
| E3 | Human review → offline export → fresh setup rehearsal | Repair concrete integration defects found in the normal operator path | Actual review, independently verified export, reload and interrupted-flow evidence | E2 and real reviewer; no invented feedback |
| R1 | GitHub App checks/publication: orchestrator `github_*` | Finish outbound publisher and unknown-outcome reconciliation; remaining installation lifecycle and credential isolation | Actual scoped App delivery/publication | App installation/key provisioning; existing repository PR work is separately authorized |
| R2 | Production identity and deployment: API config/auth, `infra/aws` | Production identity integration and deployment configuration gaps | Fresh install, tenant isolation, recovery and capacity evidence | Provider configuration; deployment/live migration approval |
| R3 | Windows/NVDA: `packages/at-adapters/nvda` | Narrow driver built; native Windows host/runner and capture composition pending | Supported Windows reader qualification and real journey | Windows host and reader permission |
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

## Hosted owner workflow and first-workspace setup — 2026-09-16

Modern frontend PR #222 merged as `49306f221ddd55cd010b6c8397fa5f865d78b84c`.
GitHub issuer compatibility PR #223 passed exact-head CI at
`74d73afaedff6c45801eda074dc39b97abedf16d` and merged as
`9f1c403c03d9471a98f768d1855365fcc011edc8`. Its source was deployed to the existing
Railway web service as `818ce086-7701-4a6e-87c7-8bcde53d5435` before merge.
Actual public-profile GitHub OAuth reached the authenticated OWNER workspace selection
and overview. Workspace creation and verified numeric GitHub identity binding used audited
operator functions; this is real login evidence, not actual runner/model evidence.

The next code slice fixes the first-owner bootstrap deadlock: missing usage previously hid the
only allowance form. Authenticated allowance/usage reads now distinguish an absent entitlement
with the scalar `setupRequired=WORKSPACE_ENTITLEMENT` problem extension. Only this confirmed
condition offers an OWNER a first-revision form using If-Match zero; generic dependency failures
and non-owner roles do not. Initial limits are zero, never unlimited or automatically saved.
Overview now links the first-run prerequisites, and the runner matrix distinguishes adapter
implementation from per-desktop qualification. Form labels and linked inline errors are clearer.

Focused verification: frontend production build, 78 UI/client checks and two isolated PostgreSQL
route checks passed. Local development identity configuration initially prevented the route fixture
from starting; explicitly using the disabled identity provider resolved that test-only environment
conflict. No authentication guard was weakened. This slice still requires its exact-head CI/merge
and deployment confirmation. No production allowance, project, run or model grant was created.

Submission gap remains a genuine end-to-end workflow, not another decorative dashboard:
project/environment → frozen journey/manifest → qualified runner → baseline → actual Codex
diagnosis and constrained repair → independent rerun → human review/export. Schedules/grants,
membership mutations and retention edits still lack complete browser authoring surfaces.
The current user instruction excludes actual VoiceOver/AT testing; do not claim those results
or replace them with synthetic evidence. No public submission has been performed.

## Canonical execution browser continuation — 2026-09-16

First-workspace setup PR #224, head `4a2c174fd9c65c3b09c9456f779c45236713e717`, is deployed
as `c927004b-224c-4f99-973e-a96350c297cc` (SUCCESS). The authenticated production OWNER Settings
page visibly presents the first allowance form, with all unsaved limits zero. No allowance was
submitted. Required Python CI remains pending at this checkpoint; other applicable checks passed.

A separate frontend continuation repairs another genuine flow break: canonical seals reserve a
run ID before admission, but the legacy journey UI hid every manifest with a non-null run ID.
Canonical seals now have an explicit selector and exact immutable scope inspection. OWNER and
MAINTAINER can separately record bounded RUN_EFFECTS approval using the reviewed revision and
then request the approved run. Existing server authority/admission gates remain unchanged.
Unknown approval responses retain a locked payload and idempotency key; run retries also retain
their key. Other roles stay read-only. Neither receipt is presented as execution or a passing result.

Frontend production build and 40 focused screen checks passed, including four new canonical
selection/approval, unknown-response retry, role and mismatched-target regressions. These are
synthetic HTTP checks, not actual execution. Source/build registration and canonical manifest
creation still need their operator/API workflow; this browser slice does not fabricate provenance.
This continuation requires normal review, exact-head CI/merge and deployment verification.

## Journey build/manifest preparation — 2026-09-16

The next browser slice connects observed source/build registration to canonical execution sealing
on the existing journey page. OWNER and MAINTAINER can record a source/build identity or supply
an existing build ID, select a server-returned usable environment, choose only its permitted effects,
and provide exact runner/model profile digests, evaluator version, expiry and explicit budgets.
Frozen journey/assertion/fixture/policy digests are reused from the read version, never retyped.
The creation receipt refreshes the manifest selector for the separate review/approval/request flow.

This records operator observations; it does not run a build, upload retained artifact bytes, verify
provenance, deploy a target, start a reader or invoke a model. Identity observability defaults false.
Unknown write outcomes lock their payload and retain the same idempotency key. Environment refresh
is offered only before a usable sealing form exists, so it cannot discard an unresolved seal key.
No production build identity, seal, allowance or execution approval was created in this slice.

Production frontend build and 46 focused screen/contract checks passed. Three new preparation
regressions cover source/build-to-seal composition without approval/run side effects, unknown
registration retry and invalid-input refusal; the final field-label/UUID tightening was rechecked
with those three tests. HTTP is synthetic. Required CI, normal review and deploy remain separate.

## Canonical run lifecycle recovery — 2026-09-16

The journey execution panel now reads the actual reserved run record. Only a confirmed 404 permits
a new request; loading, unavailable and mismatched records do not become absence. Matching admitted
runs expose their recorded status/outcome and an existing-run link instead of another request.
After an unknown request response, the panel reads the same reserved identity and links to the
admitted run when present, without another POST. If still absent, retry keeps the original key.
Approval and run refresh retain component state and pending identities. A reconciled record replaces
the obsolete unknown-response notice; it does not claim actual execution passed.

Frontend build and eight focused canonical screen checks passed, including existing-run navigation,
unavailable/mismatched record refusal and lost-response reconciliation. HTTP is synthetic; no actual
reader, model or production run was started. The full baseline/repair/rerun acceptance remains open.
This code slice requires normal source review, required exact-head CI and deployment.

## Offline build observation and source provenance correction — 2026-09-16

Preparing an automatic registration payload exposed two source identity defects: a requested
non-checked-out revision could be paired with current working-tree bytes, and ignored bytes were
hashed while status could still claim a clean commit. Source observation now refuses a revision/HEAD
mismatch, includes ignored paths in dirty status, preserves NUL-delimited rename and unusual paths,
and refuses observed HEAD/status changes across hashing. Repository fsmonitor hooks are explicitly
disabled; a real Git fixture proves the observation does not execute one.

The offline `accessforge_persistence.build_observation` module measures a stable dedicated checkout
and a supplied regular artifact outside it, producing the existing registration body without
network/DB/session access. Artifact reads are no-follow/nonblocking and checked across observation.
Identity observability remains false. It does not establish a source-to-artifact causal relationship,
atomic snapshot, retained bytes or actual deployment identity. See the build-observation handoff.
No actual source/build identity from the user's production target was registered or fabricated.

Sixteen focused source/observation checks passed against temporary real Git/filesystem fixtures;
Ruff and strict mypy passed. No database fixture was selected, no full local suite ran, and no
reader/provider call, deployment or production mutation occurred. Exact-head CI/merge remains due.

## Offline observation draft import — 2026-09-16

The journey build form now accepts the offline collector's seven-field JSON payload. Import validates
field types, full digests, exact dirty/path agreement and false deployment observability before
replacing the local draft. It makes no network write and resets the explicit target-identity
confirmation. Invalid imports leave draft values intact; unknown registration outcomes lock import
alongside the other fields and retain the original payload/key for retry. Changed paths are edited
as a JSON array rather than trimmed lines, preserving spaces and newlines in filenames.

Frontend production build and 20 focused parser/preparation tests passed. This is synthetic HTTP
UI evidence, not a production registration or actual reader/model run. Required CI, merge and
deployment remain separate. The full baseline/repair/rerun acceptance remains open.

## Pre-seal navigation configuration preview — 2026-09-16

The model-consent scope previously exposed the default navigation profile only after a run existed,
while creating that run's seal already required the profile digest. A workspace-authorized read-only
navigation-profile route now exposes the repository's existing Codex default profile and canonical
digest before any run exists. The seal form previews its complete configuration and usage disclaimer;
only an explicit choice copies the digest into the draft. No invocation, disclosure consent or
runtime qualification is inferred. Manual exact custom digests remain possible. Malformed previews
are refused, and the existing fieldset lock protects unknown seal outcomes.

Production frontend build, focused UI/contract checks, Ruff and API mypy passed. The new real-HTTP
authorization/tenant/configuration regression is committed for required CI, not claimed locally run.
Generated OpenAPI and both operation clients were refreshed. No actual provider or reader call,
production configuration write or deployment occurred. PR227 passed required CI and merged at
9719e6f; local main was synchronized. Subsequent stacked work still awaits its gates.
