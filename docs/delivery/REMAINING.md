# Remaining work, priority-ordered

## Current delivery checkpoint — 2026-09-14

Inspected baseline: `main` at `987bb7fcedc5634f6f798c954dba1818294b4284` (PR #126).
This checkpoint supersedes the historical branch-status statements below; it is not a claim of
production readiness. `PLAN.md` remains the requirement map and handoffs retain scoped evidence.

Merged implementation now includes these paths; none establishes actual reader acceptance:

- Original fixture and environment identity reconstruction in the finalizer. PR #89 is merged
  (`4006f030f9b74d4a3f0b35b723edc08eb56cbfa9`), not pending. `finalize_execution.py` consumes
  `observed_fixture`, `observed_environment`, model/runtime measurements and source/build linkage.
- Original baseline source preparation, fenced build, create-only artifact retention, protected
  fixture/runtime and owned endpoint. Explicit baseline reader binding, bounded admission and
  acknowledged-STOP cleanup are implemented; they do not qualify or start a reader by default.
- Baseline action/effect one-shot delivery, original runtime measurements, explicit receipt
  namespace and source-to-build lineage. These preserve actual provenance rather than copying
  expected identities from the seal.
- Frozen protected functional-validation predicates and producer-authored conditions. Original
  baseline receipts now feed the shared required FUNCTIONAL_REGRESSION artifact/finalizer path.
  Functional rule authoring controls are merged in PR #119. Forbidden-effect coverage still
  needs an independent trusted producer; a final zero count cannot establish absence of earlier
  transient or external effects.
- Native keyboard-focus collection, private action-bound admission/retention, frozen exact
  role/identifier-digest evaluation, retained S3/finalizer composition and authoring controls
  are merged in PRs #120–125. This is AX keyboard focus, not the VoiceOver cursor. Native compile
  checks and synthetic fixtures do not establish actual Safari identifier stability or reader
  acceptance. The retained focus finalizer proof is nonbaseline, not the baseline composition below.
- Post-STOP retention/evaluation command (PR #117), original baseline admission-to-native dispatch
  handoff (PR #118), and bounded raw-byte GitHub webhook authentication (PR #126) are merged.

### Implemented on the open GitHub integration stack, not merged at this checkpoint

| PR | Implemented boundary | Still not established |
| --- | --- | --- |
| #127 | Durable workspace/App body-digest and delivery-alias inbox | Event dispatch authority |
| #128 | Exact App/installation/repository read-access HTTP probe and temporary-token cleanup | Actual GitHub App access |
| #129 | Forced-RLS immutable repository binding and irreversible local disconnect | Current remote access or publication authority |
| #130 | Live local user/session/owner rechecks, probe-to-binding composition, atomic success audit | Deployed authenticated connection endpoint and isolated credential broker |
| #131 | Offline bounded RS256 App JWT signer | Real key provisioning/rotation or App registration |
| #132 | Exact-binding receipt-only HTTP ingress and replay composition | Deployed multi-workspace routing, event processing or publication |

Each row has scoped local checks, not actual GitHub delivery or publication evidence. Consult
current GitHub exact-head CI/merge state before integration; do not restart already-running CI.
See the [module 20 requirement handoff](../handoffs/20.md) for the missing end-to-end R1 work.

### Next implementation and acceptance gates

The `baseline_completion.execute_and_complete` entrypoint now composes the explicitly supplied
reader callback with protected-runtime closure and then original-spool retention/finalization.
It never finalizes inside the reader callback or retries execution after uncertainty. This is
an implementation ordering boundary, not the full baseline DB/S3 or physical acceptance proof
below. Operator controller configuration and explicit reader startup consent remain required.

`baseline_reader_dispatch.admit_dispatch_and_wait_reader` now keeps the trusted runtime callback
alive after native delivery acknowledgement, polling for positive original-run/attempt/runner/lease
and epoch STOP evidence. Its total timeout includes dispatch; timeout/cancellation does not replay
dispatch, release a lease or grant a verdict. The callback caller must still supply the qualified
transport and private journal; runtime closure and finalization follow outside the callback. Focused
unit checks and the baseline real-DB binding case pass with synthetic desktop observations. This
does not close the full baseline S3 or physical acceptance gates below.

Functional snapshot assembly now also joins the original `run_attempt`, desktop lease and run
manifest before stamping the artifact's attempt/producer identity. A caller cannot re-label a
valid functional receipt with another attempt or manifest. The baseline DB regression now creates
and uses the actual attempt row instead of a free-form synthetic attempt label; native/process
observations remain synthetic. Its focused case passed locally. Candidate-side regression
additions require CI: local execution stopped during object-store setup (port 9000 unavailable),
not at a product assertion. This still does not close the full baseline retained-S3 proof below.

1. **Composed baseline retention/finalization proof.** Baseline DB integration currently exercises
   original seed, endpoint, reader lease/STOP, protected receipt, snapshot and frozen functional
   assertion consumption with synthetic process/native observations. It does not execute the whole
   baseline `retain_bundle` → real S3 → `finalize_execution` path. Complete that composition without
   substituting fabricated observations for physical acceptance. Existing nonbaseline S3 finalizer
   integration and separate baseline archive/S3 tests prove only their own boundaries.
2. **Trusted host controller composition.** `runProvisionedNavigatorExecution` joins the private
   native action bridge and owned navigator process, but remains an embedding API. Finish concrete
   operator-owned configuration, runtime-evidence callbacks, action authorization and independent
   observer closure. Preserve one-shot session handling, retained uncertainty and separate startup
   consent; do not introduce permissive callback defaults to make the first run pass.
3. **Remaining assertion producers.** Required announcement, reader NEXT sequence, independent
   task-completion count, protected functional validation and native keyboard-focus predicates have
   implemented paths. Forbidden effects still need trusted measurements and frozen typed predicates.
   A final zero count does not prove that no forbidden effect occurred earlier. Actual native focus
   qualification remains separate; missing measurements remain UNKNOWN.
4. **Actual reader qualification and full product flow.** VoiceOver `VERIFIED_MATRICES` is empty.
   After explicit operator approval, qualify the actual host and retain original physical evidence;
   then execute baseline → diagnosis → constrained repair → independently approved candidate rerun
   → human review. Neither synthetic desktop integration nor Linux CI proves this acceptance path.
   Windows/NVDA physical acceptance remains separate and unproven.
5. **Release acceptance.** Reconcile every original module requirement against current code and
   runtime evidence, then complete remaining pilot/deployment and release checks. Existing API/UI,
   verifier and backup tests are evidence for their own boundaries, not a substitute for an actual
   matched reader run or deployed isolation. No completion percentage is established here.

Safe implementation and focused checks may proceed now. Billable model calls, actual AT startup,
OS permission changes, live migrations and deployment require separate explicit approval. Required
CI must complete before exact-tested-head merge and local main synchronization. Historical notes
below must not be used to re-implement already merged work or to claim missing modules are complete.

## Historical delivery notes

Historical baseline: `main` at `7c9e0bf` (PR #30 merged).

`PLAN.md` rows 08, 12 and 13 are **stale**: PR #30 landed the VoiceOver runner boundary, the
constrained Strands navigator and the bounded diagnosis projection. Their rows still read
"not started". Corrected as part of P1's handoff.

## Buildable now

### Current branch checkpoint — final evaluation snapshot

Draft PR #37 now includes original frozen rules, independent final observer conditions, post-STOP
artifact retention and atomic final evaluation storage/read API/export reasons. See handoff11 for
the operator command and evidence boundaries. These branch changes are not yet merged.

The rule-authoring UI now supports the implemented exact phrase/action and independent request-count
families, with server-provided bounds and field-linked errors (handoff22). Further predicate families
still need both trusted producers and their own authoring controls.

The Run screen now displays the original retained evaluation, per-assertion provenance/references,
outcome reasons and sealed/observed identity comparison (handoff23). It does not derive a new verdict
or present historical snapshots as proof of current artifact retention. Missing/mismatched records
remain unavailable, not invented results.

Native Safari origin wiring is now packaged with an operator diagnostic and authenticated-runner
factory (desktop native README/handoff08). This supersedes earlier local/uncommitted status. The
positive physical Safari path is not proven, and this read-only probe neither starts VoiceOver nor
completes focus/effect authorization or full execution bootstrap. No profile gate has been relaxed.

Physical preflight now binds the assigned audit session to console/process observations, samples
the supervisor clock and rejects stale/drifted reads. The physical Safari factory wires this to the
origin guard. The physical factory now also acquires a durable per-desktop claim shared across
runner registrations, guards the adapter, and releases only after clean STOP/journal/server ACK.
Crash/cancellation/unknown claims are retained for reconciliation. This requires one trusted host
root; it does not sandbox malicious same-user code or protect arbitrary low-level runner callers.
Genuine setup/deployment/capture evidence and canonical runtime identity ingestion remain pending.

The trusted execution bootstrap now joins the desktop claim, one-shot native machine session and
lazy Guidepup startup. Actions wait for explicit initialization, fresh startup authorization and
full post-start preflight. Cancellation/late startup cannot reopen the runner; an already-entered
SDK call remains uncertain and keeps the claim. The empty verified matrix is checked before any
reservation/network/SDK loading. This is an embedding API, not a deployed daemon or a completed
controller action transport; startup-consent and genuine evidence/observer producers still need
deployment integration. No OS permission or actual reader was changed to implement it.
Startup now shares the runner's absolute lease deadline/monotonic clock and rechecks authorization
after the SDK/postflight before admitting actions; a longer initialization timeout cannot extend
expired authority. Author review is scoped to this lifecycle delta, not independent PR approval.
The bootstrap's execution-authority recheck is now backed by a real machine-only server endpoint,
with exact session/attempt binding, revocation/expiry and original policy wall-budget checks. It
does not create or substitute for explicit operator consent to SDK preference/restart effects.

The current finalizer can only establish INCONCLUSIVE, because actual physical identity and
execution-preflight observations are not integrated. Missing/corrupt artifacts instead refuse
completion. Next build work is trusted runtime identity/probe ingestion and the remaining
typed predicate producers. Real VoiceOver execution, deployed service/spool
isolation and matched repair proof remain unverified; stored snapshots do not close E0/R1.

Local verification now defaults to changed-code build/typecheck only; newly authored regressions
run on CI, not in repeated local suites. CI is not disabled. The previous CI head's Node
response-validation test hit a100ms scheduling race; its
test-only override was removed in favor of the existing2-second fixture budget, preserving both
assertions and the separate deterministic deadline-expiry case.

### ~~P1 — Purge pipeline operations~~ — merged (PR #31, `42d83cc`)
All four acceptance criteria met; see `STATUS.md`.

<details><summary>Original entry</summary>

The four debts `STATUS.md` records against FR-020, all still present on `7c9e0bf`.

| # | Debt | Acceptance criteria |
|---|---|---|
| 1 | No worker drains the purge queue. A deletion the object store could not finish waits for a human to call the retry route. | A worker sweeps every workspace with pending keys, on an interval, with graceful shutdown. Discovery refuses to run as a role that cannot bypass RLS, because an invisible queue and an empty one are the same query result. |
| 2 | `purge_pending_objects` holds one transaction across up to 200 blocking store calls; `purge_until_drained` loops up to 50 such passes inside one transaction. | A drain commits per batch. A failure partway through does not roll back keys already purged — today it does, leaving bytes gone and the queue still claiming them. |
| 3 | `evidence_object_purge.artifact_id` has no foreign key, so nothing stops it naming a non-existent or cross-workspace artifact. | Composite `(artifact_id, workspace_id)` FK, added `NOT VALID` then validated. Composite because FK checks bypass RLS. |
| 4 | No test drives the deletion route with an `Idempotency-Key`. | A replayed key returns the stored response and records no second deletion. The known gap — phase one commits on its own connection, so a request failing after it leaves a deletion with no idempotency record — is tested for what it actually does, not asserted away. |

**Depends on:** nothing. **Risk if skipped:** the product promises bytes are removed and, after any
store outage, nothing removes them.
</details>

### ~~P2 — Module 14/15 routes~~ — merged (PR #32, `3ed20db`)
 Six routes, the patch path policy, `PATCH_APPLY`
approval persistence with a dispatch recheck, and every gate that refuses VERIFIED. Handoffs in
`docs/handoffs/14.md` and `15.md`.

**What is deliberately not built:** module 14's sandbox and candidate build (needs a containment
boundary), and module 15's rerun (needs a real reader). **Runtime proof is BLOCKED** and the VERIFIED
path is exercised with evidence the tests name `_fabricated_`. Nothing is marked verified.

Largest remaining hole: the gates trust what a caller reports about a candidate run. When a real
runner exists those fields must come from evidence, not claims.

Maintainer review round 1 closed three defects: the proposal now persists and reloads exact change
bytes, modes and deletions; `baseSourceDigest` is verified against the source snapshot the manifest
sealed; and the dispatch approval check compares against the patch's current revision, with approval
minting reordered after the transition so a legitimate approval stays dispatchable.

Round 2 closed a fourth: the digest check on load was conditional on there being changes, so an
emptied proposal passed it while keeping its approval.

### ~~P3 — Rate limiting~~ — merged (PR #33, `ea0aa4e`)
 Per-principal and per-workspace token buckets in PostgreSQL, enforced
at the single `build_context` chokepoint for authenticated mutating `/v1/workspaces/` routes, refused
as RFC7807 `RATE_LIMITED` with `Retry-After`. Handoff in `docs/handoffs/26-rate-limits.md`.

**Not covered:** the two session routes (`POST /v1/sessions`, `DELETE /v1/session`), which have no
trustworthy key without a client address from a proxy. Limits are global configuration rather than
per workspace. Neither is hidden: both are asserted or recorded.

### ~~P4 — Structured telemetry~~ — merged (PR #34, `d2bd3c9`)
 One structured record per request from a single ASGI
middleware: route template (never a path), method, status, outcome, duration, request id and stable
problem code. Handoff in `docs/handoffs/26-telemetry.md`.

**The privacy boundary is a whitelist, not redaction**, and it excludes tenant identifiers — so the
records answer what the API is doing and cannot answer what a customer is doing. No metrics backend,
no traces, and `streamed` describes the headers rather than the body's fate.

<details><summary>Original entry</summary>

Request and outcome telemetry with no evidence content and no object keys in it.
**Depends on:** 18.
</details>

### ~~P5 — Python dependency scanning in CI~~ — merged (PR #35, `49747cb`)
`scripts/audit_python_dependencies.py` audits `uv.lock`
rendered with `--frozen`, runs first in the security job, installs nothing and executes no package
code. Handoff in `docs/handoffs/ci-python-dependency-scanning.md`.

**Current result: 96 third-party packages, 0 advisories.** No remediation was needed and none was
applied. The scanner is a locked dev dependency, so its version and hashes are pinned like everything
it audits, and twelve mutation checks cover the ways the gate could go green while auditing nothing.

**Limits:** the audited set is the one that resolves on Linux (eleven packages carry environment
markers), the input floor is a floor rather than an equality, and every advisory fails at every
severity because pip-audit cannot grade many of them.

### ~~P6 — `mypy` over `tests/`~~ — merged (PR #36, `fa60a6f`)
The refreshed baseline had 234 errors across 29 test files, not the previously recorded 139.
CI now checks every Python workspace member, operator scripts and the complete test tree with
strict mode unchanged. Typed helpers, explicit row/cookie preconditions and complete fault-store
interfaces replace invalid annotations; negative tests remain. Two CI-configuration tests guard
the target list and prevent test exemptions. See `docs/handoffs/test-suite-mypy.md`.

### Module 14 continuation — candidate preparation and isolated build
In progress on `feat/isolated-candidate-build`. The new build-worker package prepares bounded,
immutable candidate source bytes without executing or extracting repository content on the host.
Isolated owned-reference builds, retained canonical artifacts, protected backend regressions,
durable endpoint lifecycle, materialized source/build identities, and live candidate seal/fixture/
first-lease binding, exact manual approval and one-shot committed controller handoff are implemented
on draft PR #37. One-time exact supervisor-ticket reception and a private-config/stdin native
bootstrap receiver with durable replay fencing are implemented; production reader action
transport remains unavailable by default. A bootstrap receipt does not authorize an OS action.
Automatic expired/revoked/lease-lost manual-handoff recovery now has a scoped periodic worker;
it interrupts/quarantines but never restarts the desktop.
Native machine sessions, current-authority action intents, one-shot dispatch commitments and exact
result acknowledgements are connected through real HTTP. The authenticated runner now composes
the local fsynced journal, mandatory live physical guards and existing reader-adapter interface.
Lost acknowledgements or ambiguity fence further input; ambiguous results interrupt/quarantine.
Reader observations now travel through the session into the canonical sequencer, bound to one
dispatched action, with source digests and explicit fixture redaction; capture UNKNOWN remains
distinct from silence. This bridge was exercised with synthetic physical adapters, not a live reader.
An operator-configured independent observer worker now binds the sealed credential reference and
fixture to a bounded read-only application-database measurement, then revalidates before retaining
an OBSERVER effect record. It has real PostgreSQL/CLI proof, not production deployment or reader proof.
New sessions declare five required artifacts and retain authenticated lifecycle/action receipts.
Successful STOP fences input; the independent observer closes its own final sample, then exact
reader/action/lifecycle tails and STOP acknowledgement move the run only to FINALIZING. Native
journal readback must match issued commands. Missing artifacts remain visible; no outcome is minted.
A trusted operator artifact command now validates the private supervisor spool against retained
actions and derives the other four artifacts from the closed canonical streams. Write-ahead
quarantine reservations, create-only storage and bounded readback make interrupted retention
resumable with identical bytes; corrupt, conflicting or deleted evidence is never overwritten.
This has real PostgreSQL/MinIO/CLI proof with synthetic journals, not deployed spool attestation.
Still pending: concrete production physical guards and reader startup, observer deployment/role
isolation, trusted spool/service deployment and final verdict admission,
remaining action/build UNKNOWN recovery,
and actual matched
failure-to-repair proof. See `docs/handoffs/14-candidate-build.md` for checkpoint-specific tests;
neither synthetic desktop metadata nor backend regressions establish an actual-reader repair.

Frozen executable assertion rules now round-trip through journey authoring and the immutable
reviewer contract with sealed-digest verification. Literal action-bound reader derivation and
independent observer-authored final effect-count conditions are implemented, preserving UNKNOWN
and keeping expectations out of navigator policy. No prose inference or historical backfill.
Remaining evaluator work includes final evidence/identity admission and persisted verdict, typed
focus/order/effect-monitor/functional predicates and trusted producers, plus UI rule authoring.

### P7 — `fixture_digest` offline-guess exposure
An unkeyed SHA-256 over low-entropy fixture values; anyone holding an export can test guesses
offline. A keyed commitment was refused because CONTRACTS requires offline verification. Open
trade-off needing a decision, not an implementation. **Depends on:** a decision from the owner.

## Blocked — not buildable here

| Module | Needs |
|---|---|
| 08 real VoiceOver execution | A Mac with VoiceOver automation grants |
| 09 real NVDA execution | A Windows host with NVDA |
| 12, 13, 14 real-model proof | Bedrock credentials **and** separate spend approval. The code boundaries exist and are tested against fakes; no run has invoked a real model. |
| 20 GitHub checks | GitHub App authorization |
| 25 fault laboratory | 12 proven against a real model, plus a real reader |
| 28, 29 release proof and final review | Everything above |
| All E0 acceptance | An authorized target application |

Nothing in P1–P6 requires any of these.
