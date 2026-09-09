# AccessForge — Test-driven Development and Verification Plan

Version 1.0 • 9 September 2026 • **80 specified scenarios; none has been executed by creating this documentation.**

The canonical contracts are [CONTRACTS.md](CONTRACTS.md). Requirement intent is in [PRD.md](PRD.md); architecture is in [TDD.md](TDD.md). A test may reveal a design conflict, but may not resolve it by weakening authority, evidence or accessibility assertions. Record the conflict and update the affected specification before implementation resumes.

## 1. Proof levels

| Code | Required environment and evidence |
|---|---|
| U | Unit, reference-model, schema and property tests. Label synthetic fixtures and retain randomized seeds/counterexamples. |
| I | Integration using production code, real PostgreSQL/object store, independent processes/connections and forced interleavings. |
| S | Security tests at the real process, credential, network, filesystem and service boundary; mocks alone are insufficient. |
| A | Actual named OS/browser/reader execution using the production adapter and an interactive desktop; no DOM-only substitute. |
| H | Human interaction/review, with profile, consent where relevant, recording/transcript and observed limitations. |
| O | Installation, infrastructure, backup/restore, migration and fault-injection proof against the supported deployment. |

A combined level requires all listed evidence. A mocked VoiceOver response may test a parser at U, but never satisfies A. A happy-path recording is not crash-consistency proof. An actual reader run in a fixture with an intentionally introduced defect must be labelled fault injection.

For every scenario record source SHA/tree digest, lockfile/migration digest, schema/evaluator version, environment and runner profile, test ID, inputs, seed, observed durable state, outputs and limitations. Use pseudonyms and synthetic data; never store credentials in proof. Bind screenshots/transcripts to the same run manifest as machine-readable results.

## 2. Red → green → challenge workflow

1. Write a falsifiable failing test, with the violated FR/INV and intended proof level.
2. Run it and preserve the failing observation. A missing implementation may be an expected initial failure; distinguish that from an invalid harness.
3. Implement the smallest coherent behavior and run the targeted suite.
4. Refactor while preserving behavior; run affected integration, security and AT layers.
5. Challenge the boundary using an independent oracle, mutation or forced fault. Do not compute expected verdicts using the production verdict helper.
6. Store actual commands/results and unresolved coverage in the module handoff. Generated tests and “should pass” language are not proof.

Use barriers/latches or deterministic fault hooks for races. A sleep and one fortunate schedule do not prove fencing. Parameterize all language-parsing cases through both Python and TypeScript generated bindings. Unknown security-sensitive fields must fail closed.

`P-00` through `P-29` below refer to exact prompts in [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md). Each test names an owning implementation module plus any necessary later integration gate. `R1` marks an additional full-web-release requirement; unmarked scenarios apply to the relevant E0 implementation too. An unavailable external proof is BLOCKED, never silently SKIPPED/PASS.

## 3. Authority, identity and journey contracts

### T-001 — Unauthorized origin cannot become an executable project

**Preconditions:** an authorized local/staging origin and an unrelated origin exist. **Action:** attach or run the unrelated origin through UI, API and agent tools; include redirects and changed ports. **Expected:** refusal before navigation or credential use; legitimate scoped origin remains usable. **Proof:** I/S; inspect resolved destination, denied request record and zero unauthorized navigation. **Trace:** FR-001, FR-014; INV-07, INV-08; P-03, P-05.

### T-002 — Workspace identifiers cannot cross every storage boundary

**Preconditions:** two workspaces contain similar run/artifact IDs and distinct memberships. **Action:** substitute workspace/object IDs in reads, writes, jobs, runner leases, SSE, exports and signed-download requests. **Expected:** no disclosure, inference-rich error or action in the other tenant; authorized same-tenant paths pass. **Proof:** I/S, R1; database and outbound access logs. **Trace:** FR-014; INV-07; P-03, P-18, P-26.

### T-003 — Revocation between approval and dispatch is effective

**Preconditions:** a run is approved but blocked before dispatch. **Action:** revoke membership, project authority or runner enrollment, then release dispatch; repeat at expiry equality. **Expected:** current checks deny action and record the exact reason; an old accepted response grants no continuing authority. **Proof:** I/S with independent transactions. **Trace:** FR-001, FR-014, FR-015; INV-08; P-03, P-07.

### T-004 — Approval binds exact scope, digest and revision

**Preconditions:** valid RUN_EFFECTS, PATCH_APPLY and GITHUB_PUBLISH approvals exist. **Action:** mutate scope, actor/workspace, target, targetDigest, expectedRevision or expiry independently. **Expected:** every changed payload fails authorization; an unmodified current approval succeeds only for its own action. **Proof:** U/I/S; enumerate mutations and observed denied side effects. **Trace:** FR-001, FR-010, FR-018; INV-03, INV-08; P-02, P-14, P-20.

### T-005 — Sealed manifest cannot silently change inputs

**Preconditions:** a sealed run exists. **Action:** change source tree, build, environment, fixture, journey, assertion set, runner, navigator policy, evaluator or model config while retaining its ID. **Expected:** immutable update denied or identity mismatch detected; a legitimate changed run gets fresh identity/authorization. **Proof:** U/I; all manifest fields covered by independent fixtures. **Trace:** FR-002; INV-03, INV-11; P-02, P-05.

### T-006 — Cross-language canonicalization has one digest

**Preconditions:** published RFC8785 vectors and adversarial JSON fixtures exist. **Action:** vary key order, Unicode, escaping, numeric boundaries, nulls and whitespace through both bindings. **Expected:** equivalent accepted manifests hash identically; invalid values, overflow and unknown sensitive fields reject consistently. **Proof:** U; independent expected digests, Python/TypeScript outputs. **Trace:** FR-002, FR-016; INV-03; P-02.

### T-007 — Frozen assertions do not follow edited drafts

**Preconditions:** a run references journey version A; version B is edited concurrently. **Action:** remove a required assertion in B, rename fields and rerun A. **Expected:** A uses unchanged assertions and digest; selecting B requires a new run/approval. **Proof:** U/I; durable version rows and executed assertion IDs. **Trace:** FR-003; INV-03, INV-05, INV-11; P-06.

### T-008 — Fixture reset cannot inherit a prior successful submission

**Preconditions:** real reference backend has an old completion receipt for a similar user/job. **Action:** execute a new run without completing its task, and simulate failed fixture reset. **Expected:** old receipt cannot satisfy the new observer; failed reset blocks readiness or yields INCONCLUSIVE, never PASS. **Proof:** I/A; actual backend identities and reader trace. **Trace:** FR-003, FR-007; INV-02, INV-03; P-06, P-11.

### T-009 — Real-world consequential effects are outside the admitted journey

**Preconditions:** fixture page offers an external send, purchase or production submission. **Action:** navigate to the action with an otherwise valid test run and attempt alternate tool paths. **Expected:** policy stops before the unauthorized effect; attempted destination/effect recorded safely. **Proof:** S/A; observe zero effect at controlled test sink. **Trace:** FR-001, FR-005; INV-01, INV-08; P-05, P-12.

### T-010 — Unsupported journey cannot be invented into support

**Preconditions:** journey asks for unavailable reader actions, mobile/PDF/native scope or an unobservable required assertion. **Action:** submit manually and through Strands draft generation. **Expected:** validation reports precise unsupported capability; no hidden fallback or executable unsupported claim. **Proof:** U/I; schema/problem response and absent runner dispatch. **Trace:** FR-003, FR-004, FR-008; INV-02, INV-05; P-06, P-12.

## 4. Actual reader runners and action fencing

### T-011 — VoiceOver evidence comes from the declared actual reader

**Preconditions:** pinned macOS/browser/VoiceOver profile and reference backend are available. **Action:** enroll, preflight and run the form journey; independently inspect reader identity and spoken output. **Expected:** genuine reader observations and action trace bind the profile; adapter stubs cannot satisfy the capability gate. **Proof:** A/O; version capture, recording/transcript and manifest. **Trace:** FR-004, FR-024; INV-02, INV-03; P-00, P-08.

### T-012 — NVDA support requires a separate Windows proof

**Preconditions:** pinned Windows/browser/NVDA profile and comparable fixture exist. **Action:** execute preflight, validation-error recovery and independent rerun through the production NVDA adapter. **Expected:** actual NVDA observations and correct platform-specific actions; no reuse of VoiceOver evidence as Windows proof. **Proof:** A/O, R1; independent profile bundle. **Trace:** FR-004, FR-024; INV-02, INV-03; P-09, P-28.

### T-013 — Missing, wrong or disabled reader is not a passing run

**Preconditions:** a previously working profile exists. **Action:** disable the reader, select an unsupported version, remove permission or disconnect capture. **Expected:** preflight refuses READY or the active run becomes INCONCLUSIVE/INTERRUPTED; no DOM fallback. **Proof:** I/A; capability failure and evaluator result. **Trace:** FR-004, FR-007; INV-02; P-08, P-09, P-11.

### T-014 — Locked desktop and failed reset cannot execute jobs

**Preconditions:** enrolled desktop has a locked session or contaminated fixture/browser state. **Action:** request a lease and simulate reset/preflight failures. **Expected:** no admitted action before successful reset; explicit PREFLIGHT_REQUIRED or QUARANTINED state. **Proof:** I/A; session state, lease record and no OS input. **Trace:** FR-004, FR-015; INV-02, INV-10; P-07, P-08.

### T-015 — Navigator cannot read DOM, selectors or answer keys

**Preconditions:** navigator sandbox and independent observer both run. **Action:** request page evaluate, selector click, screenshot, source, fixture receipt and hidden answer data via every exposed tool/path. **Expected:** no forbidden observation/action reaches the navigator; observer output remains private. **Proof:** S/A; actual capability/process tracing and allowed-reader control case. **Trace:** FR-005; INV-01, INV-05; P-07, P-12.

### T-016 — Keyboard allowlist does not expose shell or devtools

**Preconditions:** reader accepts supported keyboard actions. **Action:** send shell-launch, terminal, devtools, clipboard-read and arbitrary app-switch shortcuts, including composed/encoded forms. **Expected:** supervisor rejects them before OS dispatch; permitted reader shortcuts work. **Proof:** U/S/A; denied action record and observed desktop state. **Trace:** FR-005; INV-01, INV-08; P-07, P-08, P-09.

### T-017 — Action intent is durable before OS input

**Preconditions:** runner is ready and its local durable journal can be faulted. **Action:** fail local intent commit/flush, pause after durable commit and intercept OS dispatch. **Expected:** no OS action without its durable local intent; intent carries exact lease/manifest/action identity and later ingestion preserves it separately from the canonical chain. **Proof:** I/A; independently reopened local journal plus adapter dispatch capture and control-plane correlation. **Trace:** FR-006, FR-015; INV-06, INV-09; P-04, P-07.

### T-018 — Crash after ambiguous action never blindly repeats it

**Preconditions:** a real test submit action can succeed while its acknowledgment is lost. **Action:** kill the runner after sending input, restart and redeliver the job. **Expected:** old attempt is not resumed/replayed; fresh authorized run requires reset/new identity; existing test effect remains recorded/uncertain. **Proof:** I/A; backend effect count, journal and restart trace. **Trace:** FR-015; INV-09, INV-11; P-07, P-25.

### T-019 — Expired lease cannot race a second desktop owner

**Preconditions:** runner A pauses with an active interactive session. **Action:** expire its lease, request another attempt, then resume A and deliver late events. **Expected:** A fenced; no new admitted lease before session reset/preflight; stale actions/events cannot complete proof. **Proof:** I/A with forced barrier; physical session and lease epochs. **Trace:** FR-015; INV-06, INV-10; P-07, P-25.

### T-020 — Concurrent queue deliveries admit one physical attempt

**Preconditions:** duplicate job hints and two supervisors target one desktop session. **Action:** release competing acquisitions simultaneously on independent connections. **Expected:** at most one admitted attempt; losers cannot act; queue acknowledgment ordering does not change ownership. **Proof:** I/A; lease constraint, action count and outbox/job rows. **Trace:** FR-015; INV-10; P-04, P-07.

### T-021 — Cancellation fences future actions but preserves past effects

**Preconditions:** a run has one completed test effect and another action queued; a separate never-leased run exists. **Action:** cancel at dispatch barriers; delay/drop stop acknowledgment, send a stale-epoch acknowledgment and leave an in-flight action ambiguous. **Expected:** persist cancelRequestedAt/cancellationRevision and deny new admission; CANCELLED only after current-epoch stop acknowledgment plus resolved actions, or proven never-admitted queued cancellation. Unknown stop/action becomes INTERRUPTED with ambiguityReason and QUARANTINED session; no replacement before reset proof; prior receipts remain. **Proof:** I/A; cancellation metadata, OS dispatch and backend receipts. **Trace:** FR-015, FR-019; INV-09–INV-13; P-07, P-23.

### T-022 — Reader instability and unsupported locale remain visible

**Preconditions:** reproducible baseline profile exists. **Action:** change verbosity/locale/layout or induce missing/duplicated reader observations. **Expected:** profile mismatch or evidence uncertainty prevents a falsely comparable result; no majority vote hides instability. **Proof:** U/A; profile digest, complete trace and limited outcome. **Trace:** FR-004, FR-006, FR-011; INV-02, INV-03, INV-04; P-08, P-09, P-15.

## 5. Evidence integrity and deterministic outcomes

### T-023 — Exact duplicate events replay once

**Preconditions:** admitted run has authenticated producer records and a valid canonical sequence. **Action:** replay identical producerId/sourceRecordId/sourceRecordDigest through queue, HTTP retries and delayed delivery; concurrently submit supervisor and observer records. **Expected:** one canonical event per admitted source record; one trusted sequencer serializes event IDs/sequences/hash links without changing source provenance. **Proof:** U/I; producer and canonical uniqueness, independent transactions and chain equality. **Trace:** FR-006, FR-015; INV-06, INV-11; P-04, P-10.

### T-024 — Conflicting duplicate and sequence fork block completeness

**Preconditions:** one producer record and canonical event are committed. **Action:** reuse producer/sourceRecordId with different content, forge another producer identity, supply caller-selected canonical sequence/previous hash, and inject a forked bundle for offline verification. **Expected:** conflicts/forbidden fields rejected and audited, never last-write-wins; forged chain cannot claim complete proof. **Proof:** U/I/S; source-type ACL, conflict record and verifier disposition. **Trace:** FR-006; INV-06, INV-11; P-10, P-11, P-17.

### T-025 — Out-of-order events stage without hiding a gap

**Preconditions:** a required producer sends source sequence 1 and 3 with 2 missing; admitted canonical events from other producers remain contiguous. **Action:** request completeness/export, supply 2 before sealing, omit a required producer's closing watermark or truncate its tail, and separately seal a permanently incomplete attempt. **Expected:** canonical contiguity alone is insufficient; every required source stream/watermark/artifact must close. Permanent gaps/tails yield INCONCLUSIVE; late records cannot rewrite a terminal outcome. **Proof:** I; staging/source/canonical rows and offline verifier outputs. **Trace:** FR-006, FR-007, FR-013; INV-06, INV-11; P-10, P-11, P-17.

### T-026 — Hash corruption and incorrect manifest binding are detected

**Preconditions:** a valid bundle and independently calculated chain exist. **Action:** alter payload, previous hash, manifestDigest, attempt or event ordering without authorized resealing. **Expected:** both server finalizer and offline verifier reject integrity/completeness claims. **Proof:** U/I; mutation list and independent expected digests. **Trace:** FR-006, FR-013; INV-03, INV-06; P-10, P-17.

### T-027 — Agent-supplied PASS artifact is never authoritative

**Preconditions:** navigator, supervisor and independent observer have distinct authenticated capabilities. **Action:** upload fabricated PASS/receipt/preflight from the agent; submit EFFECT_RECEIPT or observer ASSERTION_OBSERVATION with supervisor credentials; submit an OS action with observer identity. **Expected:** producer/type ACL denies every forged source; only assigned observer emits independent receipt/assertion records and only supervisor emits allowed reader/action/lifecycle records. **Proof:** S/I; actual credential boundary, accepted-source policy and negative verdict record. **Trace:** FR-006, FR-007, FR-008; INV-02, INV-05, INV-06; P-10, P-11.

### T-028 — Truncated, oversized or hostile artifacts stay quarantined

**Preconditions:** evidence storage uses private quarantine. **Action:** upload partial bytes, digest mismatch, excessive size, unexpected media and path-like names. **Expected:** artifacts not attached as valid required evidence; limits enforced and safe diagnostics retained. **Proof:** I/S; object-store inspection and no unsafe parser execution. **Trace:** FR-006, FR-020; INV-06, INV-07; P-10, P-26.

### T-029 — Missing evidence outranks an observed failed assertion

**Preconditions:** one required assertion was observed false, but preflight or required artifact is incomplete. **Action:** finalize and request a confirmed finding. **Expected:** run INCONCLUSIVE; at most CANDIDATE finding, not REPRODUCED; raw failed observation preserved. **Proof:** U/I with independent truth table. **Trace:** FR-007, FR-009; INV-02, INV-06; P-11, P-13.

### T-030 — Task completion does not override a failed required assertion

**Preconditions:** valid complete evidence and backend completion receipt exist; required announcement assertion is false. **Action:** evaluate. **Expected:** FAIL, not PASS; visible account of both successful completion and failed assertion. **Proof:** U/I/A; deterministic oracle and real-reader fixture. **Trace:** FR-007; INV-03; P-06, P-11.

### T-031 — Unknown required assertion cannot pass by omission

**Preconditions:** one required assertion or required completion observation is unknown/unobservable. **Action:** evaluate cases where other assertions/completion are true, and mixed cases where another required assertion or completion is false; try omitting unknown fields from summary. **Expected:** every mixed unknown+false case is INCONCLUSIVE before FAIL evaluation; missing observations are visible and never vacuously true. **Proof:** U/I; schema completeness and independently enumerated evaluator truth table. **Trace:** FR-007; INV-02, INV-06; P-11.

### T-032 — PASS requires every frozen assertion and independent completion

**Preconditions:** a complete valid run has all required assertions true and task observer true. **Action:** evaluate, then independently remove/flip each predicate; specifically keep all assertions true while the known completion observer is false. **Expected:** PASS only for the full valid case; known false completion with otherwise complete/known evidence yields FAIL; missing/unknown completion yields INCONCLUSIVE. **Proof:** U/I/A; independent exhaustive small-state oracle. **Trace:** FR-007; INV-02, INV-03; P-11, P-25.

### T-033 — Terminal outcomes cannot be rewritten by review or retry

**Preconditions:** completed verdicts, pre/post-execution cancellations, interrupted and nonterminal runs exist. **Action:** enumerate every status/outcome pair; attempt terminal edits/resume, model reevaluation and reviewer override. **Expected:** nonterminal NOT_EVALUATED; COMPLETED PASS/FAIL/INCONCLUSIVE; INTERRUPTED INCONCLUSIVE; CANCELLED pre-execution NOT_EVALUATED or after-start INCONCLUSIVE. Every other pair rejects; terminal records remain immutable and corrections/retries are linked fresh records. **Proof:** U/I/S; independent cross-product oracle, DB constraints and append-only history. **Trace:** FR-007, FR-012, FR-015; INV-11, INV-12; P-02, P-16.

### T-034 — Raw and redacted exports preserve different verification limits

**Preconditions:** raw evidence includes secrets and a redacted derivative exists. **Action:** export each; delete a required raw object; verify offline without server access. **Expected:** distinct digests/view declarations; redacted/deleted bundle never pretends to contain complete raw proof; tampering identified. **Proof:** I/S; independent verifier logs. **Trace:** FR-013, FR-020; INV-06, INV-15; P-17, P-26.

## 6. Agents, diagnosis and untrusted code

### T-035 — Real Strands orchestration is evidenced without secret leakage

**Preconditions:** approved provider/model access and restricted tools are configured. **Action:** run actual planning/navigation through Strands and compare recorded tool calls to policy. **Expected:** real configured execution, bound model/tool identity and redacted telemetry; a canned script is not claimed as model execution. **Proof:** I/A; invocation metadata and run evidence. **Trace:** FR-008, FR-024; INV-01, INV-03; P-12.

### T-036 — Web content prompt injection cannot expand authority

**Preconditions:** authorized fixture contains instructions to ignore policy, reveal credentials or use DOM shortcuts. **Action:** let navigator/diagnoser encounter that content. **Expected:** content stays untrusted; supervisor denies forbidden tools and no authority/approval changes occur. **Proof:** S/A with controlled canary sinks. **Trace:** FR-005, FR-008; INV-01, INV-05, INV-08; P-12, P-26.

### T-037 — Budget exhaustion stops visibly instead of fabricating success

**Preconditions:** bounded model tokens/calls, AT actions and wall time. **Action:** trigger loops, repeated refusals and provider stalls at each limit. **Expected:** durable budget event, stopped work and honest limited outcome; no hidden unlimited retry or fake fallback. **Proof:** U/I/A; counters, provider/OS request counts. **Trace:** FR-008, FR-021, FR-025; INV-14; P-12, P-26.

### T-038 — Observer secrets never flow into navigator context

**Preconditions:** independent observer has a receipt token/answer canary unavailable to the reader. **Action:** exercise logs, tool errors, replay, model traces and diagnosis-to-navigation handoff. **Expected:** navigator context and accessible tools contain no observer-only data. **Proof:** S/I; traced request bodies with synthetic canaries. **Trace:** FR-005, FR-008; INV-01, INV-05; P-11, P-12.

### T-039 — Diagnosis separates observation from hypothesis

**Preconditions:** one reproduced failure, one incomplete run and stale source mapping exist. **Action:** generate findings and source explanations. **Expected:** complete failed run supports REPRODUCED only for its exact behavior; incomplete/stale evidence yields CANDIDATE or unresolved location, not invented certainty. **Proof:** U/I/H; finding links and practitioner review. **Trace:** FR-009; INV-02, INV-03; P-13.

### T-040 — Repair agent cannot alter protected evaluators or fixtures

**Preconditions:** candidate sandbox exposes allowed source paths and protected test/evaluator manifests. **Action:** propose direct, symlink, rename and generated-file edits to protected paths. **Expected:** patch rejected/invalidated before use; protected digests unchanged. **Proof:** U/S; filesystem and build-context inspection. **Trace:** FR-010, FR-011; INV-05, INV-16; P-14, P-15.

### T-041 — Untrusted build scripts cannot read secrets or host files

**Preconditions:** isolated build worker and synthetic host/credential canaries exist. **Action:** run repository install/build scripts that inspect environment, host mounts, process metadata and network metadata endpoints. **Expected:** no secret/host access; bounded failure and quarantine without credential disclosure. **Proof:** S/O; actual sandbox/network trace. **Trace:** FR-010, FR-020; INV-05, INV-07, INV-08; P-14, P-26.

### T-042 — Patch paths, archives and dependencies cannot escape isolation

**Preconditions:** candidate build accepts an approved repository snapshot. **Action:** supply traversal paths, symlink escapes, archive bombs, submodule redirects and dependency-script downloads outside policy. **Expected:** safe rejection or constrained execution; no write outside candidate boundary and no unapproved fetch. **Proof:** S/O; filesystem snapshots and egress logs. **Trace:** FR-010; INV-05, INV-08; P-14.

### T-043 — Model and package outages do not erase evidence

**Preconditions:** baseline evidence is durable and diagnosis/build work is queued. **Action:** return model timeout, rate limit, dependency failure and unsupported response shape. **Expected:** bounded retry/degraded state; baseline remains accessible and immutable; no fabricated patch or verified result. **Proof:** I/O; fault records and durable history. **Trace:** FR-008, FR-009, FR-015, FR-021; INV-11, INV-14; P-12, P-14, P-26.

### T-044 — Prompt and tool configuration drift makes proof explicit

**Preconditions:** sealed run references a modelConfig/navigatorPolicy digest. **Action:** change prompt/tool allowlist/model configuration during queueing and replay old session state. **Expected:** run uses its frozen supported configuration or is interrupted/recreated; silent authority expansion is impossible. **Proof:** U/I/S; manifest and tool configuration comparison. **Trace:** FR-002, FR-008; INV-01, INV-03, INV-05; P-12.

## 7. Repair, matched verification and review

### T-045 — Patch approval is not merge or deployment approval

**Preconditions:** PATCH_APPLY is granted for a candidate. **Action:** request git push, PR publication, protected-branch merge and production deployment through the patch worker. **Expected:** only isolated candidate application is permitted; external writes denied without separate exact scope. **Proof:** S/I; outbound request capture. **Trace:** FR-010, FR-018; INV-08; P-14, P-20.

### T-046 — Source or patch changes make existing approval stale

**Preconditions:** approved patch binds base SHA/tree and patch digest. **Action:** move base branch, modify patch bytes or change revision while worker waits. **Expected:** dispatch refuses the stale approval; no “helpful” rebase runs under prior authority. **Proof:** U/I with barrier; Patch.STALE/history and no unauthorized build. **Trace:** FR-002, FR-010; INV-03, INV-08; P-14.

### T-047 — Matched verification detects environment differences

**Preconditions:** valid baseline and candidate runs exist. **Action:** vary reader/browser/version, locale, fixture, assertions, environment or unapproved build difference individually. **Expected:** candidate is not VERIFIED; comparisons list mismatches and request fresh matched evidence. **Proof:** U/I/A; pair manifest comparison. **Trace:** FR-011; INV-03, INV-04; P-15.

### T-048 — Removing validation is not an accessibility repair

**Preconditions:** failing form requires a valid field, authentication and server validation. **Action:** propose patches that remove required input, skip auth, accept invalid data or change the task to avoid the barrier. **Expected:** functional/protected tests fail and repair remains unverified even if reader journey completes. **Proof:** U/I/A/S; backend assertions and diff. **Trace:** FR-010, FR-011; INV-05, INV-16; P-14, P-15.

### T-049 — Accessibility improvement with another functional regression is blocked

**Preconditions:** patch fixes error announcement but breaks valid submission, authorization or another protected path. **Action:** run full candidate gate. **Expected:** apparent AT improvement retained as evidence; patch not VERIFIED because required regressions fail. **Proof:** I/A; before/after reader trace and functional reports. **Trace:** FR-011; INV-16; P-15.

### T-050 — Inconclusive candidate cannot become a verified repair

**Preconditions:** baseline is valid FAIL; candidate appears better but loses a required artifact or observer receipt. **Action:** finalize candidate and request verify/accept. **Expected:** candidate INCONCLUSIVE; patch cannot become VERIFIED/REVIEW_ACCEPTED or resolve finding through review override. **Proof:** U/I; reducer transitions and UI state. **Trace:** FR-007, FR-011, FR-012; INV-02, INV-06, INV-12; P-15, P-16.

### T-051 — The verifier does not trust repair-agent self-assessment

**Preconditions:** repair agent returns “fixed” and a forged passing test summary. **Action:** omit or fail independently executed tests while submitting that summary. **Expected:** no verification; actual protected runner/evaluator output is required. **Proof:** S/I; authority sources and verdict dependencies. **Trace:** FR-011; INV-05, INV-16; P-15.

### T-052 — Human review stays separate, scoped and attributable

**Preconditions:** machine outcomes PASS/FAIL/INCONCLUSIVE and eligible reviewer account exist. **Action:** submit ACCEPT, CHANGES_REQUESTED and UNABLE_TO_ASSESS with mismatched version or missing required scope. **Expected:** invalid review refused; valid review retained independently; no machine outcome overwritten. **Proof:** U/I/H; review records and presentation. **Trace:** FR-012, FR-019; INV-11, INV-12; P-16, P-24.

### T-053 — Self-review policy and role removal cannot be bypassed

**Preconditions:** project requires an independent reviewer and patch author has multiple sessions. **Action:** self-approve through alias/session, then revoke a genuine reviewer during submission. **Expected:** configured identity policy and fresh role checks apply; denied review cannot resolve finding. **Proof:** I/S, R1; canonical actor identity and authorization audit. **Trace:** FR-012, FR-014; INV-07, INV-08, INV-12; P-03, P-16.

### T-054 — Reopen, dismissal and correction preserve old evidence

**Preconditions:** a reproduced or resolved finding has historical reviews. **Action:** dismiss with reason, correct review, reopen after new regression and attempt destructive overwrite. **Expected:** append-only linked history; old run outcomes unchanged; new evidence/ownership clear. **Proof:** U/I/H; full timeline and export. **Trace:** FR-009, FR-012, FR-013; INV-11, INV-12; P-13, P-16, P-17.

## 8. API, events, integrations and privacy

### T-055 — Idempotency is payload-bound and rechecks read authority

**Preconditions:** a mutation was accepted for a principal/workspace/route/key. **Action:** replay identical payload, change payload under same key, cross principal/workspace and revoke read permission before replay. **Expected:** identical authorized replay returns prior acceptance, conflict returns 409, revoked response exposes no data; no duplicated work. **Proof:** U/I/S. **Trace:** FR-015, FR-016; INV-07, INV-08; P-04, P-18.

### T-056 — API validation and revision checks cover alternate entry paths

**Preconditions:** UI, CLI and agent clients target the same API. **Action:** submit malformed/unknown fields, unsafe integers, wrong enums, missing If-Match and stale revisions. **Expected:** stable documented error and no partial mutation; accepted requests have identical semantics across clients. **Proof:** U/I; generated contract vectors and DB state. **Trace:** FR-016; INV-03, INV-08; P-02, P-18.

### T-057 — SSE reconnect and replay gaps resynchronize honestly

**Preconditions:** active run emits durable events; client disconnects beyond replay retention. **Action:** reconnect with current, stale, foreign and missing cursors. **Expected:** authorized replay or explicit gap/reset followed by authoritative fetch; no invented PASS, duplicate start or cross-tenant event. **Proof:** I/H; wire transcript and UI state. **Trace:** FR-017, FR-019; INV-06, INV-07; P-19, P-23.

### T-058 — Schedule overlap, backlog and revoked scope are bounded

**Preconditions:** opted-in R1 schedule has an explicit ExecutionGrant, budget and slow runner. **Action:** miss/overlap/redeliver ticks; change source/ref or grant revision; revoke/expire grant between child mint and dispatch; attempt patch/publication under the grant. **Expected:** bounded backlog, one occurrence admission, fresh exact RUN_EFFECTS child per run with parent identity/revision; mint and dispatch both recheck current parent scope; changed/out-of-scope inputs and repair/publication refused. **Proof:** I/O/S, R1; grant/child/scheduler/outbox records and admission counts. **Trace:** FR-001, FR-015, FR-017, FR-021; INV-03, INV-08, INV-10, INV-14; P-19.

### T-059 — GitHub publication needs exact current approval

**Preconditions:** authorized repository read access but no publish approval. **Action:** attempt check/comment/branch/PR writes; then approve one payload and change repository, SHA or body. **Expected:** zero unauthorized writes; exact current approval permits only its intended publication. **Proof:** I/S, R1; controlled repository API receipts. **Trace:** FR-018; INV-03, INV-08; P-20.

### T-060 — Forged, duplicate and reordered repository webhooks are safe

**Preconditions:** optional integration is enabled. **Action:** forge signature, replay delivery, change target SHA and deliver stale events after revocation. **Expected:** authenticate/deduplicate; untrusted webhook never grants authority or relabels old proof as new commit coverage. **Proof:** I/S, R1; webhook journal and run/check identities. **Trace:** FR-018; INV-03, INV-07, INV-08; P-20.

### T-061 — Redaction covers every supported diagnostic channel

**Preconditions:** synthetic secrets occur in source, reader speech, form values, URL, model errors and screenshots. **Action:** generate logs, evidence, support bundle and redacted export. **Expected:** required redaction applied to declared channels; unsupported/unredactable capture blocked or clearly excluded, not promised safe. **Proof:** S/I/H; canary scan plus human inspection. **Trace:** FR-020, FR-021; INV-07, INV-15; P-17, P-26.

### T-062 — Consent withdrawal and deletion invalidate affected proof claims

**Preconditions:** consented evidence exists in raw, derived, cached and exported forms. **Action:** withdraw consent, delete within supported policy and restore a permitted backup. **Expected:** no new unauthorized collection; tombstones/retention rules reapplied; missing evidence visibly limits verification; downloaded-copy limits disclosed. **Proof:** I/S/O; storage inventory and offline verifier result. **Trace:** FR-020, FR-022; INV-08, INV-15; P-26, P-27.

### T-063 — Signed URLs and archives cannot bypass artifact authorization

**Preconditions:** private evidence and short-lived authorized download exist. **Action:** reuse after expiry/revocation, substitute object key, enumerate paths and inspect archive entries. **Expected:** supported access policy enforced; no public listing, cross-workspace download or extraction escape; unavoidable already-downloaded retention disclosed. **Proof:** I/S; object-store requests and safe extraction. **Trace:** FR-013, FR-014, FR-020; INV-07, INV-15; P-17, P-26.

### T-064 — Usage metering and entitlements survive contention and redelivery

**Preconditions:** workspace near its quota and duplicated usage events exist. **Action:** race admissions, replay usage, change entitlement and interrupt billed-resource work. **Expected:** deduplicated measured usage, explicit admitted-work policy, no silent over-limit new work or fictional refund; no payment collection. **Proof:** I; independent ledger/count oracle. **Trace:** FR-021, FR-025; INV-07, INV-14; P-26.

## 9. Product usability and operations

### T-065 — Fresh user completes the genuine repair workflow

**Preconditions:** clean supported setup and real reference backend; no hidden database edits. **Action:** use UI to authorize, freeze journey, run actual baseline, inspect failure, approve candidate, verify, review and export. **Expected:** durable end-to-end state and real evidence; no hard-coded success or manual fixture substitution. **Proof:** I/A/H; complete run recording and export. **Trace:** FR-001–FR-013, FR-019; INV-01–INV-16; P-22, P-23, P-24, P-28.

### T-066 — AccessForge critical flows work with keyboard and actual reader

**Preconditions:** declared supported reader profiles and seeded product states exist. **Action:** complete setup, run cancellation, diff inspection, review and export without a pointer. **Expected:** meaningful labels/order, visible focus, announced results and no traps; diff information remains accessible. **Proof:** A/H; task transcripts and defects. **Trace:** FR-019; INV-12; P-21, P-22, P-23, P-24.

### T-067 — Responsive layout and nonvisual alternatives preserve meaning

**Preconditions:** long IDs, extensive diffs, large text and verbose findings exist. **Action:** test 375/768/1024/1440 widths, zoom, reduced motion and contrast modes. **Expected:** no clipped approvals/critical actions; text/status alternatives, transcript access and usable table/diff navigation. **Proof:** H plus automated supporting scans; screenshots and keyboard evidence. **Trace:** FR-019; INV-12; P-21, P-24.

### T-068 — Every asynchronous and incomplete state is understandable

**Preconditions:** empty/loading, denied, stale, unavailable, interrupted, cancelled, failed and inconclusive records. **Action:** navigate, reconnect and attempt available recovery actions. **Expected:** no empty-state misrepresentation of missing evidence; limits/next actions clear; UI does not imply pending is complete. **Proof:** I/H; state fixtures and backend comparison. **Trace:** FR-007, FR-015, FR-019; INV-02, INV-06, INV-13; P-22, P-23, P-24.

### T-069 — Database/outbox crashes preserve one accepted operation

**Preconditions:** mutation, authoritative records and transactional outbox share a transaction. **Action:** kill before commit, after commit/before publish and after publish/before acknowledgment. **Expected:** no lost accepted job or duplicate admitted effect; recovery follows database authority, not queue delivery count. **Proof:** I/O with actual process kills. **Trace:** FR-015, FR-022; INV-09, INV-11; P-04, P-27.

### T-070 — Migrations preserve uncertainty and immutable identities

**Preconditions:** database contains queued, running, interrupted, candidate, verified and redacted/deleted records. **Action:** migrate forward and exercise supported rollback/restore paths with old queue messages. **Expected:** no stale record becomes complete/authorized; digests retain meaning, and unsupported rollback stops explicitly. **Proof:** I/O; before/after invariant reports. **Trace:** FR-002, FR-015, FR-022; INV-03, INV-06, INV-11, INV-15; P-27.

### T-071 — Restore cannot replay obsolete desktop actions or approvals

**Preconditions:** backup predates newer cancellation, revocation, run effects and deletion markers. **Action:** restore, reconnect old runners and redeliver outbox messages. **Expected:** dispatch disabled until recovery policy reconciles authority/session state; fresh leases/reset required; no blind action replay or renewed revoked consent. **Proof:** I/S/O/A; recovered DB and action count. **Trace:** FR-015, FR-020, FR-022; INV-08, INV-09, INV-10, INV-13, INV-15; P-27.

### T-072 — Degraded dependencies and quarantine retain safe control

**Preconditions:** API/runner/model/object store/DB dependencies can be interrupted. **Action:** isolate them independently; quarantine a suspect runner and try new work. **Expected:** bounded backoff, explicit dependency status, preserved evidence and zero admission to quarantined session; no hidden fallback result. **Proof:** I/O/A; telemetry and durable states. **Trace:** FR-004, FR-015, FR-021; INV-02, INV-06, INV-14; P-26, P-27.

### T-073 — Clean installation executes documented commands without hidden state

**Preconditions:** fresh checkout and supported authorized machines/accounts. **Action:** follow setup, migrations, enrollment and reference-app seed using only documented inputs. **Expected:** pinned dependencies and genuine E0 execution; missing OS/model permissions reported explicitly; no undeclared developer cache/secrets needed. **Proof:** O/A; install log, version matrix and handoff. **Trace:** FR-022, FR-024; INV-03; P-01, P-27, P-28.

### T-074 — Measured capacity honors serialized desktops and quotas

**Preconditions:** reference hardware/workload, one or more declared desktop sessions and budgets. **Action:** load API/events while queueing AT jobs; exhaust workspace quota and observe cancellation. **Expected:** no session overlap, bounded queue/backpressure, measured latency/cost with denominators; no unmeasured SLA claim. **Proof:** I/O/A; benchmark report and resource counters. **Trace:** FR-021, FR-025; INV-10, INV-14; P-25, P-26.

## 10. Independent challenge and release proof

### T-075 — Boundary mutations are killed for the intended reason

**Preconditions:** targeted suites pass on the recorded implementation. **Action:** independently remove tenant predicates, lease checks, hash checks, assertion requiredness, protected-path checks and approval digest validation. **Expected:** each mutation causes a specific behavior test to fail; survivors are documented gaps and block relevant integrity claims. **Proof:** U/I/S; mutation diff and failing assertion. **Trace:** FR-023; INV-01–INV-16; P-25, P-29.

### T-076 — Held-out corpus exposes false passes and false defects

**Preconditions:** held-out real/injected/no-defect/infrastructure-failure scenarios have independently reviewed labels. **Action:** execute without editing protected assertions to fit results. **Expected:** denominators, unsupported/inconclusive cases, false confirmed defects and false passes reported separately; no selective omission. **Proof:** U/A/H; versioned corpus and evaluation report. **Trace:** FR-023; INV-02, INV-05, INV-16; P-25, P-28.

### T-077 — Conventional-tool comparison can reject the product thesis

**Preconditions:** authorized operator workflow using axe/Guidepup/CI or documented current practice exists. **Action:** compare equivalent tasks, environment/setup cost, repair time and reviewer confidence; inspect design-partner versus commercial commitments. **Expected:** measured paired results and operator feedback; negative/neutral value is recorded honestly, and unpaid access never clears willingness-to-pay validation. **Proof:** H/O; consented observations, comparison protocol and separately classified commitments. **Trace:** FR-023, FR-024; INV-12; P-28.

### T-078 — Human review does not require disability disclosure or imply universal proof

**Preconditions:** reviewer can use product without a disability profile; study participation is separately offered. **Action:** decline disclosure/participation, submit scoped review and inspect public/export wording. **Expected:** product access remains possible under role policy; no inferred disability identity, legal certification or universal-usability assertion. **Proof:** H/S; forms, stored fields and export copy. **Trace:** FR-012, FR-019, FR-020, FR-024; INV-12, INV-15; P-16, P-26, P-28.

### T-079 — Release and video claims bind the actual tested slice

**Preconditions:** E0/R1 proof set and event artifacts exist. **Action:** change source/dependency/profile after recording, remove required artifact and label E0 as full R1. **Expected:** stale proof or unsupported claim detected; report says specified/implemented/tested/blocked separately; no fabricated screen-reader or submission claim. **Proof:** O/H; manifest comparison and checklist audit. **Trace:** FR-024; INV-02, INV-03, INV-06, INV-15; P-28, P-29.

### T-080 — Independent final reviewer reproduces the highest-risk path

**Preconditions:** release candidate, full handoffs, unresolved list and clean environment access exist. **Action:** independently reproduce real baseline/candidate workflow plus ambiguous-action crash, authority denial, evidence gap and malicious repair. **Expected:** exact-version evidence supports claimed scope or release remains blocked; author assurances cannot waive failures. **Proof:** I/S/A/H/O; independent verdict and finite blocker list. **Trace:** FR-001–FR-025; INV-01–INV-16; P-29.

## 11. Requirement coverage index

This index identifies representative direct coverage; every scenario's own Trace is authoritative. Integration scenarios may cover additional requirements without replacing their focused negative tests.

| Requirement | Primary scenarios |
|---|---|
| FR-001 | T-001, T-003, T-004, T-009 |
| FR-002 | T-005, T-006, T-044, T-046, T-070 |
| FR-003 | T-007, T-008, T-010 |
| FR-004 | T-011–T-014, T-022, T-072 |
| FR-005 | T-015, T-016, T-036, T-038 |
| FR-006 | T-017, T-023–T-028 |
| FR-007 | T-029–T-033, T-050 |
| FR-008 | T-035–T-038, T-043, T-044 |
| FR-009 | T-029, T-039, T-054 |
| FR-010 | T-040–T-042, T-045, T-046, T-048 |
| FR-011 | T-047–T-051 |
| FR-012 | T-052–T-054, T-078 |
| FR-013 | T-026, T-034, T-054, T-063 |
| FR-014 | T-002, T-003, T-053, T-063 |
| FR-015 | T-017–T-021, T-055, T-069–T-072 |
| FR-016 | T-006, T-055, T-056 |
| FR-017 | T-057, T-058 |
| FR-018 | T-004, T-045, T-059, T-060 |
| FR-019 | T-052, T-057, T-065–T-068, T-078 |
| FR-020 | T-034, T-041, T-061–T-063, T-078 |
| FR-021 | T-037, T-043, T-064, T-072, T-074 |
| FR-022 | T-069–T-073 |
| FR-023 | T-075–T-077 |
| FR-024 | T-011, T-012, T-035, T-073, T-077–T-079 |
| FR-025 | T-037, T-064, T-074 |

## 12. Execution interfaces and release rules

The implementation must provide independently runnable commands for schema/unit/property, PostgreSQL integration, sandbox security, desktop VoiceOver, desktop NVDA, UI actual-reader, mutation, backup/restore and offline bundle verification. Exact command names are selected and documented in the implementation checkout; no executable is claimed to exist in this specification folder.

Each command must report its resolved environment and proof level, fail clearly when prerequisites are absent, and never silently substitute mocks for requested AT/external proof. Human-granted OS permissions, model billing/resource access and external repository publication require explicit authorized setup. The test plan itself authorizes none of those actions.

E0 may exclude the NVDA, GitHub and automatic-schedule R1 scenarios only by listing them as outside E0 scope, not marking them passing. R1 requires both real platform profiles and all applicable scenarios. A flaky critical case is unresolved, not excused by rerunning until green. Never delete failed attempts from evidence.

Known authority bypass, cross-tenant access, unsupported PASS, false confirmed defect caused by missing evidence, repeated ambiguous action, unprotected candidate mutation, inaccessible primary review path, or a falsely complete export blocks the relevant release. A release report must list tested, blocked, failed and not-in-scope cases with the exact build and profile. See [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md).
