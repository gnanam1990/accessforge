# AccessForge — Product Requirements Document

Version 1.0 • 9 September 2026 • Proposed product and acceptance specification. No feature in this document is represented as implemented or tested.

## 1. Product decision

**AccessForge is a journey-to-repair engineering workspace:** reproduce a specified accessibility barrier using actual assistive technology, propose a constrained source-code repair, independently repeat the journey on the candidate build, and preserve a human-reviewable evidence trail.

The product promise is deliberately narrower than “AI makes every application accessible.” It establishes what happened for a named journey, assertion set, application build, fixture and supported reader/browser/OS profile. It does not certify legal compliance, represent all disabled people, or replace accessibility specialists and disabled-user evaluation.

The full ambition is continuous accessibility engineering across an organization's software. The first complete release is a web product. Mobile, PDF and native desktop remediation require separate designs and are not hidden inside this release.

Read [CONTRACTS.md](CONTRACTS.md) for authoritative names and boundaries, [TDD.md](TDD.md) for technical realization, [TEST-PLAN.md](TEST-PLAN.md) for proof and [UI-UX.md](UI-UX.md) for screen behavior. TDD means Technical Design Document; TEST-PLAN defines test-driven development separately.

## 2. Problem, evidence and limits

A website can load successfully, pass backend health checks and still prevent a screen-reader user from completing a critical task. Error summaries that are not announced, incorrect focus after validation, unlabeled controls, modal traps and inaccessible dynamic updates are examples. A developer receiving an issue then needs to reproduce it, locate the affected component, change the code, check that functionality was not weakened, and ensure the failure does not return.

Existing scanning and testing tools are valuable. The product is not justified by pretending they do not exist. The proposed gap is the operational handoff between an observed journey failure, an auditable patch, matched before/after execution and sustained regression ownership.

| Evidence or comparator | What is supported | What is not supported |
|---|---|---|
| [WebAIM Million](https://webaim.org/projects/million/) | Detectable accessibility defects are widespread; automated checks have explicit limits. | A homepage survey is not a measure of every full user journey or this product's revenue opportunity. |
| [Guidepup issue 99](https://github.com/guidepup/guidepup/issues/99) | A practitioner requested screen-reader automation integration and useful failure reporting. | A closed issue is neither a currently unresolved defect nor a paid customer commitment. |
| [Guidepup](https://github.com/guidepup/guidepup) | Actual VoiceOver/NVDA automation is an existing building block to evaluate and reuse. | Its availability does not prove our execution, isolation or reliability. |
| [Deque axe MCP](https://docs.deque.com/devtools-server/4.0.0/en/axe-mcp-server/) | Accessibility analysis and AI-assisted remediation already have serious competitors. | “We use AI” is not differentiation. |
| [Evinced monitoring direction](https://www.evinced.com/ai/monitoring) | Journey-oriented AI accessibility testing is an active competitive direction. | We have not established that competitors lack each proposed feature. |

The paid-pilot thesis remains unproven. No invented TAM, willingness-to-pay amount, customer count, productivity percentage or legal-risk reduction belongs in the marketing copy. [SOURCES.md](SOURCES.md) records the current research and refresh obligations.

## 3. Target users and jobs

### 3.1 First customer profile

Web agencies and frontend platform organizations maintaining several authorized web applications are the first candidate segment. They have repeatable development workflows, repository access and staging environments, and can evaluate a repair against their existing stack. This is a testable choice, not a validated acquisition channel.

| Role | Job to be done | Required result |
|---|---|---|
| Project maintainer | Connect only the repository and environment they control. | Explicit scope, a reproducible test setup and no hidden production authority. |
| QA/accessibility practitioner | Reproduce a reported barrier using a named supported profile. | A complete ordered trace, reproducible journey and an honest outcome. |
| Developer | Understand and repair a barrier without weakening the application. | Source-linked findings, a reviewable patch, functional regression results and precise limits. |
| Reviewer | Assess the repair and whether the available evidence is sufficient. | Keyboard/screen-reader-operable review, attributable decision and unresolved questions. |
| Workspace administrator | Operate the service without exposing client code or personal information. | Access policy, consent/retention controls, budgets, operational state and incident records. |

A reviewer need not disclose disability status. Recruiting disabled participants for a user study needs separate informed consent, scheduling and compensation arrangements. Neither a role label nor a machine-generated persona substitutes for actual participation.

### 3.2 Primary job statement

“When an important web journey is reported as inaccessible, help me reproduce the barrier, safely prepare a repair, and show exactly what improved without hiding uncertainty or introducing a functional/security regression.”

Secondary jobs are rerunning known journeys after a change, exporting review evidence, managing a supported runner profile and assigning remediation ownership. General website analytics, SEO scanning, legal certification and arbitrary browser automation are not product jobs.

## 4. Release scope

| Boundary | Included | Explicitly excluded |
|---|---|---|
| **E0 — working event slice** | One owner-operated workspace; one authorized web application with a real backend; seeded test data; one pinned macOS/browser/actual VoiceOver profile; form-error recovery journey; real Strands planning; baseline failure; isolated patch; candidate build; independent rerun; recorded human review; verifiable export. | Arbitrary customer code hosting; multi-tenant production claim; NVDA support claim; automatic merge/deployment; real-world consequential submissions. |
| **R1 — complete web product** | Multiple workspaces; access controls; actual VoiceOver and NVDA profiles; durable runs and recovery; patch/review workflow; UI/API/CLI; opt-in repository checks and schedules; privacy, quotas and operational support. | Every browser-reader-version combination; production transactions; native mobile/desktop/PDF support; legal certification; automatic payment collection/tax handling. |
| **R2+ — roadmap** | Potential mobile readers, native desktop applications, PDF remediation, broader languages, managed desktop fleet and enterprise procurement integrations. | Any claim these capabilities were delivered by E0 or R1. |

An E0 local signed-in desktop runner is an acceptable demonstration boundary only if the actual limitations are disclosed. The event deadline is not permission to relabel E0 as a finished R1 product. Reference-app fault injection must be labelled; an injected defect is not a discovered customer incident.

R1 billing scope is metering and administrator-configured entitlement. No checkout flow, card handling, tax computation or automatic overage charge is specified.

## 5. End-to-end product journey

1. **Authorize the project.** The maintainer identifies the repository, immutable source revision, permitted local/staging origin, fixture-reset procedure, test-only effects and credential profile. The service validates membership and permissions; a pasted URL is not proof of authority.
2. **Prepare the journey.** A Strands-assisted draft describes the user intent, safe fixture values, required assertions and independently observable completion. A person reviews and freezes the version. Unsupported operations cannot become executable merely because a model generated them.
3. **Prove runner readiness.** The selected actual reader/browser/OS combination passes preflight in a reset interactive desktop. If the capability is absent, record unavailable/inconclusive; do not silently use DOM automation instead.
4. **Seal and run baseline.** The approved manifest binds versions, effects, budgets and authority. The navigator receives only the approved task and actual reader observations. The supervisor permits only allowed actions and journals action intent before execution.
5. **Evaluate evidence.** Invalid/incomplete evidence or any unknown required assertion/completion observation yields INCONCLUSIVE before any false observation is considered. With all required observations known and valid, a false assertion or false task completion establishes FAIL; all required conditions true establishes PASS. Completing the task alone does not override a failed required assertion.
6. **Diagnose.** The repair/diagnosis process may inspect authorized source and diagnostic evidence after the failure. It produces a source-linked finding with observed facts, hypothesis, affected journey and limitations. An infrastructure interruption remains distinguishable from a reproduced accessibility defect.
7. **Propose and approve repair.** Show the exact diff and scope. Approval binds the patch digest and base revision. Build only in an isolated candidate workspace. Never grant the repair agent merge, production credentials or permission to weaken protected assertions.
8. **Independently verify.** Reset fixtures and rerun matched baseline/candidate profiles. Run functional and protected security/validation regressions. The evaluator—not the repair agent—decides whether the evidence establishes a repair.
9. **Review and export.** A person records an attributable review with the tested scope and limits. Export binds the source, journey, evidence, machine outcome and separate human decision. Redaction/deletion limitations remain visible.
10. **Continue safely.** Opt-in schedules and repository changes create new runs. Changed versions, changed assertions or fresh execution always have fresh identities. Existing evidence is not rewritten to fit a new release.

## 6. Detailed functional requirements and acceptance criteria

### FR-001 — Authorized project/repository/environment scope

The product must record the owner-authorized repository, allowed origins, credential profile and permitted test effects. Environment creation requires a role with scope-management authority. Approval text must show the resolved environment, not an ambiguous “continue” label. Expired/revoked scope prevents new dispatch. Redirects, internal-network targets and production-looking origins are rejected unless explicitly supported and authorized by policy. No automatic domain-wide discovery expands the approved boundary.

**Acceptance:** unauthorized project IDs, origin changes, production side effects and changed approvals fail before the associated action; permitted staging journeys still complete normally. Removal of integration access has a documented effect on queued work and existing evidence.

### FR-002 — Immutable source/build/environment identity

Each run seals the source commit/tree, build, environment, fixture, journey, assertions, runner profile, evaluator, navigator policy and model configuration. Display the exact tested revision and supported profile in results. A new execution creates a new run even with identical inputs. An uncommitted source tree must be explicitly identified by digest and cannot be mislabelled as a clean commit.

**Acceptance:** modifying any bound input invalidates reuse of its authorization/proof. Exports and API results agree on the immutable identities; unsupported or missing build identity blocks verified completion.

### FR-003 — Versioned journey, fixtures and assertions

Journey authoring supports draft validation, review, version history and freezing. Required assertions include relevant task steps and a separately observed completion condition. Fixture data must be synthetic or explicitly consented and resettable. A journey declares what is not being tested. Editing an assertion creates a new version, not a repair to an already executed test.

**Acceptance:** an invalid or unsupported journey cannot start; running versions stay unchanged while drafts evolve; fixture reuse cannot make a failed or already-submitted task appear successful.

### FR-004 — Actual AT runner capabilities and preflight

Show OS, browser, reader, locale, keyboard layout, verbosity and adapter versions. E0 supports only the proven VoiceOver profile; R1 additionally proves a Windows/NVDA profile. Preflight checks the actual active reader and interactive session, application reachability, reset success and evidence capture. Supported means the exact matrix passed, not the library lists the platform.

**Acceptance:** missing reader, wrong version, locked session, failed reset or disconnected capture cannot become READY for the incompatible job. Reports retain unsupported/unavailable states without a substitute success.

### FR-005 — Navigator observation/action restriction

Screen-reader-only navigation receives no DOM, source, selectors, screenshots, diagnostic observer data or answer keys. Allowed actions are enforced outside the agent. Keyboard shortcuts cannot open a shell, devtools or arbitrary system application. Out-of-policy tool calls are denied and recorded.

**Acceptance:** attempts to read hidden attributes, use direct selectors, inspect clipboard, call a shell, bypass the reader or access observer results fail at the actual process/capability boundary. A mock-denial test alone is insufficient.

### FR-006 — Complete, ordered, provenance-bound evidence

Run evidence includes ordered action intents/results, reader observations, assertions, effect receipts, preflight and finish records. Bind every event/artifact to the sealed manifest and admitted attempt. One trusted ingestion sequencer assigns the canonical chain after authenticating producer identity and permitted record type; independent producers keep their own sequences and closing watermarks. The supervisor cannot forge an independent observer receipt. Expose source-stream gaps, missing tails, upload gaps, truncation, redaction and corruption even when the admitted canonical chain is contiguous. Raw and redacted artifacts are separate objects with separate digests.

**Acceptance:** identical source-record replay is safe; conflicting source records, caller-selected canonical sequence/hash, forbidden producer types, missing producer watermarks/tails, forks, gaps, stale attempts or missing required artifacts prevent verified completion. Export verification must detect tampering independently of the live application.

### FR-007 — Deterministic and explicitly limited outcome

Run status describes execution; outcome describes the valid evidence. The canonical evaluator returns NOT_EVALUATED, PASS, FAIL or INCONCLUSIVE. Invalid/incomplete evidence, an unknown required assertion or an unknown required completion observation takes precedence over any observed failure and yields INCONCLUSIVE. If evidence is complete/valid and all required conditions are known, any false assertion or false task completion yields FAIL; only all true yields PASS. Completion is a frozen required condition, not an optional extra. LLM confidence, reviewer preference or a successful backend receipt cannot override the frozen evaluator.

**Acceptance:** fixtures independently cover each branch and ordering conflict. Nonterminal runs retain NOT_EVALUATED. COMPLETED may have PASS/FAIL/INCONCLUSIVE; INTERRUPTED has INCONCLUSIVE. CANCELLED before admitted execution has NOT_EVALUATED; CANCELLED after execution began has INCONCLUSIVE. Interrupted/cancelled runs never acquire PASS/FAIL. Every PASS lists precisely the tested journey/profile and untested scope.

### FR-008 — Real Strands orchestration and bounded agent actions

Use actual Strands execution for the planned role, with recorded model/tool configuration and restricted tools. Enforce action, wall-time and resource limits outside model prompts. Model failure may trigger bounded retries, but never a hard-coded result or silent change of evidence mode. Separate planning, navigation and diagnosis capabilities.

**Acceptance:** real configured model/tool execution is evidenced in the release slice; model outage, looping, malicious page content and tool-policy violations stop or degrade honestly. Synthetic agent fixtures remain labelled tests.

### FR-009 — Evidence-grounded diagnosis and findings

Findings retain reproduction links, exact assertions, affected versions, source locations, observed facts, hypotheses and confidence limitations. CANDIDATE is not REPRODUCED. A dismissed or resolved finding retains its history; reopened work links new evidence. No legal-conformance claim is inferred from a model's classification.

**Acceptance:** an incomplete baseline can create only a candidate observation; a complete matching failed run is required to reproduce it. Source locations cannot point to unrelated or unavailable revisions without an explicit stale/unresolved label.

### FR-010 — Isolated source patch proposal and approval

The patch agent proposes diffs in a bounded candidate workspace. Show changed files, base identity, patch digest, risk, rationale and protected-file checks before approval. PATCH_APPLY authorizes only that candidate build. Source builds are treated as untrusted execution with restricted secrets, filesystem, network and resources.

**Acceptance:** an unapproved or stale patch cannot build through the approved path; path traversal, dependency scripts, protected-test edits, weaker validation and attempted secret access are blocked or invalidate the candidate. No merge/deployment happens implicitly.

### FR-011 — Independent baseline/candidate verification

Matched verification uses the frozen journey/assertions, reset fixtures, actual reader profile and independent evaluator. Baseline/candidate differences are limited to the approved patch and explicitly recorded permitted build differences. Functional regression tests must prove the intended task and validation/security properties still hold. A candidate's INCONCLUSIVE outcome cannot establish VERIFIED.

**Acceptance:** a repair that removes a required field, authentication, validation or test assertion is rejected despite apparent task completion. A reader/browser/fixture mismatch forces new comparable evidence. Unrelated regressions keep the patch unverified.

### FR-012 — Human review with scoped attestations

Reviews record actor, role, tested journey/version/environment, verdict, notes and limits. ACCEPT, CHANGES_REQUESTED and UNABLE_TO_ASSESS are distinct. Reviews are append-only; corrections supersede with a linked record. They do not overwrite machine outcomes, confer execution credentials or establish legal compliance.

**Acceptance:** accepting incomplete proof cannot make it verified or resolve the finding; a reviewer can request changes without destroying earlier evidence. Independent reviewer requirements and exceptions are explicit policy, not silently ignored.

### FR-013 — Redacted evidence export and independent verifier

Export a machine-readable manifest plus human-readable summary, included artifacts, identities, chronology, outcome and separate reviews. An offline verifier checks schema, hashes, event chain and completeness for the declared view. Missing raw evidence after redaction/deletion remains disclosed. Provide safe accessible download behavior and no public-by-default evidence links.

**Acceptance:** tampering, missing required evidence, unsupported schema and a misleading redacted bundle fail verification or produce an explicitly limited result. An export cannot claim a service signature proves universal usability or historical truth outside its attestation.

### FR-014 — Tenant/role/service-identity enforcement

R1 isolates workspaces across API, database, jobs, runners, artifacts, event streams, exports and integrations. Separate human session, runner enrollment/lease, worker and repository identities. Use least-privilege roles for view, author, approve, review and administer; the technical role matrix lives in TDD/SECURITY.

**Acceptance:** substitution of a workspace or object identifier cannot reveal or act on another workspace. Revocation is checked at dispatch and replay/read boundaries. Service credentials do not inherit administrator rights by convenience.

### FR-015 — Durable jobs, idempotency, interruption and cancellation

PostgreSQL is authoritative for job admission and business records; the runner also persists a local durable action intent before OS input. Queue redelivery cannot duplicate a run/effect. An ambiguous action after crash must not be blindly retried. Terminal runs do not resume. Cancellation first records cancelRequestedAt/cancellationRevision and stops new admission; it is not yet proof the desktop physically stopped. Terminal CANCELLED requires current-epoch stop acknowledgment with no unresolved in-flight action, except a never-leased run proven to have admitted no action. Uncertain stop becomes INTERRUPTED with ambiguityReason and session quarantine. Already performed effects remain recorded. One physical interactive session admits at most one active attempt.

**Acceptance:** crash/restart, lease expiry, competing workers, lost response, missing/stale stop acknowledgment and late events are tested with real independent processes. UI distinguishes cancellation requested from acknowledged CANCELLED and ambiguous INTERRUPTED, explains performed effects, and never claims rollback or a verified physical stop without evidence.

### FR-016 — Validated HTTP API and SDK/CLI

Expose versioned, schema-validated operations and generated clients. Mutations enforce idempotency, current authorization and revision preconditions. Async acceptance is not completion. CLI must show workspace/environment identities before consequential actions and must never infer an unsafe default. Errors are stable machine codes plus safe human guidance.

**Acceptance:** malformed/unknown fields, stale revision, conflicting idempotency keys, overflow, forbidden resources and dependency outage produce the documented safe errors. Replayed responses cannot leak data after role removal.

### FR-017 — Replayable UI events and bounded schedules

UI reconnects using durable event IDs and explicitly handles replay gaps by resynchronizing authoritative state. R1 schedules use an explicitly approved ExecutionGrant bounded to project/environment, journey/policy versions, source-ref selection, safe effects, budgets, expiry and revision. Each occurrence resolves immutable inputs and mints a fresh exact RUN_EFFECTS child authorization with its parent grant identity/revision. Minting and dispatch both recheck the parent's current authority, expiry, scope and revocation. A schedule grant never permits patch application or GitHub publication. Missed intervals must not create an unbounded backlog. Events and scheduled tasks enforce current workspace permissions.

**Acceptance:** reconnect cannot double-start work, conceal evidence gaps or infer success. Overlap and backpressure remain within declared policy; every admitted occurrence has a fresh exact child authorization, changed/revoked grants stop minting and dispatch, and source selection cannot broaden the parent scope.

### FR-018 — Explicitly authorized GitHub/check integration

Repository attachment and outbound publication are separate permissions. Read-only preparation does not authorize comments, check runs, branches or PRs. GITHUB_PUBLISH binds the exact repository/revision/payload. Only opt-in checks may affect repository status; report tested coverage and unavailable infrastructure honestly.

**Acceptance:** no outbound write without explicit scope; stale/revoked approvals cannot publish; webhooks are authenticated/deduplicated and untrusted content cannot grant authority. Existing evidence cannot silently be represented as testing a new SHA.

### FR-019 — Accessible, modern, complete product UI

The workspace supports setup, run preparation, live trace, findings, diff/review, verification, export and administration. All critical flows work by keyboard and the supported readers. Use clear hierarchy, visible focus, contrast, reduced motion, semantic controls, transcript alternatives and non-color-only state. Loading, empty, denied, unavailable, failed, interrupted and stale states are designed, not improvised.

**Acceptance:** fresh users can perform the E0 flow without hidden database edits; R1 critical operations pass actual-reader tests. An inaccessible review or approval control blocks release. See UI-UX for detailed screens and interaction contracts.

### FR-020 — Consent, minimization, retention and deletion

Default to synthetic test data, private storage and minimal capture. Document what source, speech, recordings, diagnostics and model-provider inputs may contain. Consent and collection policy apply before run dispatch. Retention/deletion applies to primary objects, derived views, exports, caches and supported backups according to a disclosed policy; local downloaded copies cannot be remotely recalled.

**Acceptance:** redaction catches fixture secrets in all supported representations, deletion invalidates affected completeness claims and withdrawn consent blocks new collection. Reports explicitly state any retained audit metadata, backup delay and user-managed downloaded-copy limits.

### FR-021 — Resource budgets, telemetry and incident response

Enforce per-run and workspace resource limits; distinguish actual usage, estimates and unknown provider costs. Observe queue latency, runner health, evidence gaps, denial counts, verification duration and model failures without leaking sensitive content. Provide incident classification, quarantine, investigation and recovery procedures.

**Acceptance:** exhausted budget stops new work visibly; a disconnected or suspect runner cannot be reused automatically. Correlation IDs support diagnosis without raw secrets. No production reliability/SLA claim precedes measured operational evidence.

### FR-022 — Reproducible installation, deployment and recovery

Pin tested dependencies and supported platform profiles. Provide local setup and documented cloud infrastructure boundaries, secrets setup, backup/restore, migration, runner enrollment and rollback procedures. A clean checkout must reproduce the supported slice. An older restore must not replay stale approvals, leases or actions.

**Acceptance:** installation and restore are executed from clean environments, not merely described; restored records retain uncertainty and immutable proof identities. Platform/resource/account access not obtained is labelled blocked.

### FR-023 — Adversarial benchmarks and honest quality evaluation

Maintain held-out scenarios covering actual accessibility barriers, no-defect cases, unusable runner conditions and malicious repairs. Record scenario provenance and injected-vs-observed labels. Evaluate false confirmed defects, unsupported passes, replay reproducibility, regression introduction and human agreement separately. Avoid a single attractive score that hides failures.

**Acceptance:** boundary mutations cause the intended tests to fail; held-out cases are not silently used to tune protected assertions. Report actual denominators, versions and uncertainty; compare against the conventional tool stack before claiming product advantage.

### FR-024 — Working release proof, documentation and event artifacts

Deliver setup/run instructions, architecture diagram, tested version matrix, limitations, source/license disclosures, evidence bundle and a real end-to-end recording. Event materials must match the exact working slice and the actual rules at submission. Submission itself is a separate external action requiring authorization.

**Acceptance:** a fresh evaluator can follow documented setup and inspect actual-reader evidence. Video state corresponds to verifiable runs. Blocked proof is not replaced with edited success, and no document says the product is implemented merely because this specification exists.

### FR-025 — Usage metering and manually managed entitlements

Record attributable run/model/desktop resource usage with deduplicated identities and limits. Administrators can assign quotas and entitlements with an audit record. UI distinguishes measured use from cost estimates; cancelled or failed work can still consume real resources. No automatic payment collection or taxation is part of R1.

**Acceptance:** redelivered usage cannot double-charge the internal meter; tenant quotas cannot be bypassed through concurrent starts or alternate API paths. Entitlement reduction has explicit behavior for already admitted work and never falsifies completed usage.

## 7. Nonfunctional acceptance

### Security and integrity

All sixteen invariants in CONTRACTS are release-blocking. Authorization must be enforced by process/service identity and tool boundaries, not a safety prompt. Source builds, web content and model output are untrusted. No “best effort” exception may convert incomplete proof into a verified result.

### Accessibility and interaction

Primary task paths must work with keyboard plus the declared reader profiles. Automated UI scans supplement actual interaction, not replace it. Avoid autoplay recordings, motion dependence, inaccessible diff rendering and status announcements so frequent they prevent navigation.

### Performance and capacity

Measure API responsiveness, event lag, queue wait, reader action latency, build duration and cost separately. A desktop profile is serialized per physical interactive session; throughput estimates must reflect that constraint. The first benchmark report establishes reference hardware, dataset size and measurable operating budgets. Do not publish unmeasured numeric SLA/scale promises. Slow execution remains visible with bounded cancellation and honest progress.

### Reliability and recovery

Loss of a worker, desktop or provider must lead to explicit interrupted/unavailable state, not optimistic completion. Durable state and evidence survive supported restart boundaries. Fault injection is required for queue redelivery, ambiguous desktop actions, stale leases, object upload failure, restore and cancel races.

### Privacy and supportability

Support bundles are opt-in and redacted. Access to raw customer source/evidence is independently authorized and audited. A support operator cannot silently alter a run outcome, fixture or review. Data export and deletion behaviors must remain understandable without knowledge of the internal database.

## 8. Success metrics and validation gates

Metrics are proposed evaluation measures, not achieved results:

- Time from reported barrier to independently reproducible finding, compared with the customer's existing workflow.
- Time from reproduced finding to accepted, regression-checked repair.
- Share of candidate diagnoses confirmed by complete AT evidence; report the denominator and exclusions.
- Unsupported PASS and false confirmed-defect counts, with zero tolerated known integrity defects in the release corpus.
- Candidate repair regressions and protected-test violations.
- Repeatability for the same frozen profile, including inconclusive runs rather than removing them from reports.
- Human reviewer acceptance, changes requested and unable-to-assess rates, kept separate from machine results.
- Operational cost per completed/failed/inconclusive run and waiting time for actual desktop capacity.

**Customer/problem gate:** observe three target operators, reproduce two real barriers including at least one externally reported incident in an explicitly authorized environment, and obtain one consented human review of a repair. **Access gate:** obtain a written design-partner/access commitment sufficient to run the authorized workflow; unpaid access is not willingness to pay. **Commercial gate:** obtain an actual paid pilot or explicit commercial commitment with a decision-maker, scope and commercial terms. An unpaid design partner does not clear this gate. **Differentiation gate:** compare against axe + Guidepup + existing CI + specialist workflow. If customers do not perceive a meaningful advantage or will not permit the required access, revise or stop the product thesis rather than relabel negative evidence as an immature market.

## 9. Release acceptance summary

E0 is accepted only when its real working form-recovery journey, constrained authority, actual VoiceOver execution, independent candidate verification, human review and export are demonstrated at the recorded revision. It may disclose local-owner limitations; it may not claim unsupported R1 isolation or platforms.

R1 additionally requires the complete multi-workspace workflow, supported VoiceOver and NVDA matrices, operational recovery, privacy controls and all applicable named tests in TEST-PLAN. A critical accessibility failure in AccessForge's own primary path, known authority bypass, fake AT success, incomplete evidence labelled verified, tenant leak or weakened repair validation blocks release.

See [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) for dependency gates and [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) for the sign-off record. An unchecked release item is pending, not implicitly satisfied by this document.
