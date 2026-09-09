# AccessForge — canonical implementation contracts

Version 1.0 • 9 September 2026 • Proposed specification, not implemented software.

This file is the shared vocabulary for all documents and prompts. PRD owns product intent; this file owns wire names, scope, identities and state semantics; TDD owns implementation detail. A contradiction blocks the affected change until all impacted documents are reconciled. Never resolve ambiguity by weakening evidence or authority.

## 1. Release boundaries

- **E0 — working event slice:** one owner-operated workspace, one explicitly authorized web application with a real backend and seeded test accounts, one pinned macOS + browser + actual VoiceOver combination, one form-error recovery journey, actual Strands planning, reproduced failure, constrained patch, candidate build, independent rerun, recorded human review and evidence export. A single local signed-in desktop runner is allowed with its limitations disclosed. No hosted arbitrary customer code.
- **R1 — complete web product:** multi-workspace agency/platform workflow, scoped repository/environment access, actual macOS VoiceOver and Windows NVDA runner profiles, durable jobs, patch/review lifecycle, exports, authenticated UI/API, opt-in repository checks, schedules, quotas, privacy controls, deployment/restore and operational support. Support means the exact version matrix passed; not every browser/reader combination.
- **R2+ — expansion:** mobile readers, native desktop applications, PDF remediation, additional languages/readers, managed multi-tenant desktop fleet and broader procurement integrations. Roadmap items are not R1 claims. Define new threat models and acceptance tests before enabling them.

Neither release certifies legal compliance or universal accessibility. Actual screen-reader automation supplements, not replaces, disabled-user and specialist evaluation. Billing in R1 is usage metering and administrator-configured entitlement; payment collection and tax handling are outside scope.

## 2. Chosen architecture, not an existing checkout

Python/FastAPI control plane and Strands workers; PostgreSQL authoritative records/outbox; S3-compatible private evidence objects; TypeScript Node desktop-runner adapters using Guidepup; React/TypeScript web workspace; JSON Schema 2020-12 as authoritative cross-language wire contract; generated Python/TypeScript bindings; pnpm and uv lockfiles. These are proposed choices, not detected user-project dependencies. Prompt 00 selects the actual implementation checkout; Prompt 01 pins a tested supported version set.

Proposed paths:

```text
apps/api/                 Python API and authorization
apps/orchestrator/        Strands planning and diagnosis workers
apps/web/                 React product interface
apps/desktop-runner/      TypeScript supervisor and AT adapter
apps/build-worker/        isolated source/patch/build execution
packages/contracts/      JSON Schemas, generated bindings and fixtures
packages/domain/         Python pure reducers and verdict rules
packages/persistence/    migrations, repositories and transactional outbox
packages/at-adapters/     Guidepup VoiceOver/NVDA adapters
packages/evidence/       manifests, export and offline verification
packages/clients/        generated API clients and operator CLI
packages/agent-tools/    narrowly scoped tool schemas/adapters
fixtures/reference-app/  real local application and backend, seeded setup
tests/                   unit, contract, integration, security and AT suites
infra/                   deploy, runner bootstrap and restoration procedures
docs/handoffs/            numbered implementation evidence
specs/accessforge/        this unchanged specification pack
```

No app imports another app's private internals. Queue delivery is a hint; PostgreSQL decides admissible state. Local and AWS queue transports implement the same durable-job contract. Strands session persistence is not the business database.

## 3. Stable requirement IDs

| ID | Requirement family |
|---|---|
| FR-001 | Authorized project/repository/environment scope |
| FR-002 | Immutable source/build/environment identity |
| FR-003 | Versioned journey, fixtures and assertions |
| FR-004 | Actual AT runner capabilities and preflight |
| FR-005 | Navigator observation/action restriction |
| FR-006 | Complete, ordered, provenance-bound evidence |
| FR-007 | Deterministic and explicitly limited outcome |
| FR-008 | Real Strands orchestration and bounded agent actions |
| FR-009 | Evidence-grounded diagnosis and findings |
| FR-010 | Isolated source patch proposal and approval |
| FR-011 | Independent baseline/candidate verification |
| FR-012 | Human review with scoped attestations |
| FR-013 | Redacted evidence export and independent verifier |
| FR-014 | Tenant/role/service-identity enforcement |
| FR-015 | Durable jobs, idempotency, interruption and cancellation |
| FR-016 | Validated HTTP API and SDK/CLI |
| FR-017 | Replayable UI events and bounded schedules |
| FR-018 | Explicitly authorized GitHub/check integration |
| FR-019 | Accessible, modern, complete product UI |
| FR-020 | Consent, data minimization, retention and deletion |
| FR-021 | Resource budgets, telemetry and incident response |
| FR-022 | Reproducible installation, deployment and recovery |
| FR-023 | Adversarial benchmarks and honest quality evaluation |
| FR-024 | Working release proof, documentation and event artifacts |
| FR-025 | Usage metering and manually managed entitlements |

## 4. Run identities and sealed manifest

Use opaque UUID identifiers, workspace-scoped composite foreign keys and UTC RFC3339 timestamps. Ordered run events use an integer sequence, not wall-clock order. Counters remain within JSON safe-integer bounds; reject overflow. No floating-point confidence score can grant authority or decide an outcome.

`RunManifest` includes schemaVersion, workspaceId, projectId, runId, journeyVersionId, journeyDigest, assertionSetDigest, fixtureDigest, sourceCommitSha, sourceTreeDigest, buildArtifactDigest, environmentConfigDigest, runnerProfileDigest, navigatorPolicyDigest, evaluatorVersion, modelConfigDigest, authorizationId, expiresAt, actionBudget, wallTimeBudgetSeconds and permittedEffects. Secret values are excluded; references and immutable credential-profile identities may be included. Image/browser binaries, locale, keyboard layout, reader verbosity and fixture reset method are recorded in the referenced runner/environment manifests.

`manifestDigest = SHA-256(RFC8785 canonical JSON manifest)`. Use common canonicalization vectors in both languages; digests are lower-case hex. Security-sensitive objects reject unknown fields. Changes to a sealed input require a new run/authorization. Repeated execution creates a new runId and fresh fixture instance, even when source/journey are unchanged.

Approval records bind `{scope, actorId, workspaceId, targetId, targetDigest, expectedRevision, expiresAt}`. Scopes: `RUN_EFFECTS`, `PATCH_APPLY`, `GITHUB_PUBLISH`. Approval is rechecked at dispatch; changed SHA/digest, expiry, revocation or scope invalidates it. `PATCH_APPLY` authorizes only the isolated candidate workspace, not merge or production deployment. Human review is not a credentials grant.

R1 recurring runs use a separate explicitly approved `ExecutionGrant` describing workspace/project/environment, allowed journey/policy versions, source-ref selection rule, safe effects, budgets, expiry and revision. It is not an exact-run Approval. At each occurrence the trusted dispatcher resolves immutable inputs, allocates run/authorization IDs, seals the manifest and mints a fresh exact `RUN_EFFECTS` child authorization recording parent grant ID/revision and issuing service identity. Minting and dispatch recheck the parent's current scope, expiry, revision and revocation; changed inputs require a new child. A grant never authorizes repair application or GitHub publication. No schedule may broaden its grant.

## 5. State vocabulary

`Run.status`: `QUEUED → LEASED → RUNNING → FINALIZING → COMPLETED`. Any nonterminal state may end `INTERRUPTED` or `CANCELLED`. Terminal runs never resume; retry creates a linked new run. A completed execution can have FAIL or INCONCLUSIVE outcome. Infrastructure failure is not a reproduced accessibility defect.

Cancellation first records `cancelRequestedAt` and `cancellationRevision`, denies new admission and requests a runner fence. For an unleased queued run, proof that no action was admitted permits immediate CANCELLED. Otherwise terminal CANCELLED requires `stopAcknowledgedAt` bound to the current epoch and no unresolved in-flight action. While acknowledgement is absent display cancellation requested, not physically stopped. Lost acknowledgement or ambiguous dispatched action ends INTERRUPTED with `ambiguityReason` and quarantines the session. Lease timeout alone is not a verified stop; no replacement until stop/reset proof. Already performed test effects remain recorded.

`Run.outcome`: `NOT_EVALUATED`, `PASS`, `FAIL`, `INCONCLUSIVE`.

Admissible status/outcome pairs: nonterminal runs retain NOT_EVALUATED; COMPLETED has the deterministic PASS/FAIL/INCONCLUSIVE verdict; INTERRUPTED has INCONCLUSIVE; CANCELLED before any admitted execution has NOT_EVALUATED, while CANCELLED after execution began has INCONCLUSIVE. PASS and FAIL are never attached to interrupted/cancelled runs. A separate candidate observation may survive without changing these pairs.

Outcome evaluation order: evidence/identity/preflight invalid or incomplete, including any unknown/unobservable required assertion or required completion observation → INCONCLUSIVE; otherwise one or more frozen required assertions false or the frozen task completion observer false → FAIL; otherwise all frozen required assertions true and frozen task completion observer true → PASS. Required completion is a frozen required condition, not an optional extra. A raw observed failure in an incomplete run can create a CANDIDATE finding but does not turn that run into verified FAIL. No average or majority voting overrides this order.

`Finding.status`: `CANDIDATE`, `REPRODUCED`, `DISMISSED`, `RESOLVED`. REPRODUCED requires a complete valid failed run supporting the exact assertion/behavior. RESOLVED requires a verified repair and the required human review. A reviewer can dismiss or reopen with a reason but cannot rewrite a run outcome. Findings are observations about specified behavior, not legal determinations.

`Patch.status`: `PROPOSED → APPROVED → BUILDING → VERIFYING → VERIFIED → REVIEW_ACCEPTED`; terminal alternatives `REJECTED`, `STALE`, `FAILED`. Verification failure means the repair was not established, not that the original observation disappears. Changed base or patch digest makes existing approval stale. `VERIFIED` requires full matched baseline/candidate evidence, functional regressions and protected assertions; human review cannot convert an INCONCLUSIVE candidate to VERIFIED.

`Review.verdict`: `ACCEPT`, `CHANGES_REQUESTED`, `UNABLE_TO_ASSESS`; reviewer role, tested journey/version, environment and limits accompany it. No demographic/disability disclosure is required to use the product. Participation in a user study requires separate consent.

`Runner.status`: `OFFLINE`, `PREFLIGHT_REQUIRED`, `READY`, `BUSY`, `QUARANTINED`. One active lease per physical interactive desktop session. Expired lease fences the runner; no new lease until reset/preflight confirms the previous session cannot still act.

## 6. Navigator and observer separation

Navigator input contains approved task intent, safe fixture values and actual reader observations. It never receives DOM, source, selectors, screenshots, observer secrets or fixture answer keys in screen-reader-only mode. Navigator output uses allowlisted AT actions (`NEXT`, `PREVIOUS`, `ACTIVATE`, `TYPE_TEXT`, `KEY_CHORD`, `READ_CURRENT`, `WAIT_FOR_READER_IDLE`, `STOP`), bounded and validated by the supervisor. `KEY_CHORD` has a platform-specific allowlist, not arbitrary OS shortcuts. No shell, direct DOM click, arbitrary URL, downloads, clipboard read or developer-tools access.

The independent observer may read an authorized application receipt/test endpoint and capture diagnostics; none of that flows back into navigation. The repair agent may inspect source after failure, but never alter the frozen assertions, fixture oracle or verified execution policy. An agent reaching a goal through a forbidden shortcut invalidates the proof.

E0/R1 effects are restricted to test data in an owned/authorized staging or local environment. No live purchase, real application submission, email blast, production mutation, CAPTCHA bypass or external terms acceptance. An unapproved effect stops the path and records the blocked reason.

## 7. Evidence event contract

Each envelope has `{schemaVersion, workspaceId, runId, attemptId, leaseEpoch, sequence, eventId, type, sourceTime, receivedTime, manifestDigest, previousEventHash, payloadDigest, payload}`. Trusted ingestion assigns receivedTime. The source event hash excludes receivedTime and itself, canonicalizes the other source fields and chains previousEventHash. Sequence starts at 1 with a fixed documented genesis hash. Same eventId+digest replays; different digest is conflict. Unique `(runId, attemptId, sequence)` prevents forks. Out-of-order events wait in staging; no contiguous-completeness claim before gaps close.

The canonical chain has **one trusted ingestion sequencer per attempt**, serialized by database lock/transaction. Producers submit authenticated records, not caller-selected canonical sequence/previous hash. Each payload carries validated `producerId`, `sourceRecordId`, `producerSequence`, `sourceRecordDigest` and `sourceRecord` provenance. The sequencer assigns eventId/sequence/previousEventHash after identity/type admission, preserves sourceTime and hashes canonical content. Source-record replay is keyed by producer plus sourceRecordId/digest; conflict is rejected. Per-producer sequences are staged until contiguous; they are distinct from canonical sequence, which orders admitted records. The canonical hash attests ingestion provenance, not physical truth.

Only the assigned supervisor may submit reader/action/preflight/lifecycle records. Only the independent observer identity may submit `EFFECT_RECEIPT` and observer `ASSERTION_OBSERVATION`; these cannot originate from navigator, patch worker or supervisor. The evaluator may derive assertions from retained reader evidence without pretending they came from the independent application observer. Finalization requires authenticated closing watermarks for every required producer and all required artifacts; missing producer tails cannot be hidden by an otherwise contiguous ingestion chain. The runner's local durable action journal remains separate and is included as evidence.

Event kinds: `RUN_STARTED`, `PREFLIGHT_RESULT`, `ACTION_INTENT`, `ACTION_RESULT`, `READER_OBSERVATION`, `ASSERTION_OBSERVATION`, `EFFECT_RECEIPT`, `BUDGET_EVENT`, `INTERRUPTION`, `RUN_FINISHED`. Store action intent before sending to the OS. An ambiguous OS action is not automatically replayed. Restart invalidates that attempt and starts a fresh environment/run after authorization checks.

Artifacts are uploaded to quarantine, size/type checked, hashed server-side and bound to the manifest before FINALIZING can complete. No artifact supplied solely by the agent becomes trusted merely because its JSON says PASS. A manifest signature authenticates the service attestation, not the truth of a physical user experience. Raw and redacted views have different digests; retained/redacted/deleted evidence states are explicit.

## 8. Core invariant IDs

| ID | Invariant |
|---|---|
| INV-01 | Navigator cannot use a capability outside its sealed observation/action policy. |
| INV-02 | Missing AT capability/evidence never becomes PASS or an automatic confirmed defect. |
| INV-03 | Every outcome binds exact journey, assertions, source, build, fixture and runner versions. |
| INV-04 | Baseline/candidate differ only by an approved patch and explicitly recorded permitted build differences. |
| INV-05 | Agents cannot edit evaluator, protected tests, consent or their own authority. |
| INV-06 | Event gaps, forks, stale leases and corrupt/missing required artifacts prevent verified completion. |
| INV-07 | Tenant isolation covers API, rows, jobs, runners, artifacts, streams and exports. |
| INV-08 | Consequential actions require current exact-scope authorization. |
| INV-09 | Ambiguous desktop actions are never blindly repeated after restart. |
| INV-10 | One physical desktop session has at most one admitted active attempt. |
| INV-11 | Terminal records are immutable; corrections/reviews are append-only linked records. |
| INV-12 | Human opinions and machine outcomes remain separately attributable. |
| INV-13 | Cancellation fences future actions; already performed test effects remain recorded. |
| INV-14 | Budget exhaustion stops work visibly and never silently switches to a fake result. |
| INV-15 | Deletion/retention limitations cannot leave an apparently fully verifiable export. |
| INV-16 | A repair cannot weaken validation, authorization or the task to make accessibility tests pass. |

## 9. HTTP conventions

Prefix `/v1`; authenticated workspace from membership plus path, never trusted from body alone. Browser uses secure server session/CSRF protection; desktop uses short-lived workspace-bound enrollment/lease credentials. Errors use RFC7807-style problem JSON with `code`, `requestId` and safe details. Status codes: 400 invalid input, 401 no auth, 403 denied, 404 unknown/inaccessible resource, 409 stale revision/idempotency conflict, 422 unsupported journey/capability, 429 exhausted quota, 503 unavailable dependency.

Mutations take `Idempotency-Key`; same principal/workspace/route/key with same canonical body replays the accepted operation, changed body returns 409. Recheck read authorization before replaying data. Revision-sensitive mutations require `If-Match`. Async acceptance returns 202 plus operation/run ID; it does not claim a side effect completed. Events use SSE with durable IDs and explicit replay-gap/reset handling; reconnect never implies success.

## 10. Implementation handoff contract

Every module writes `docs/handoffs/NN.md` in the chosen implementation checkout: scope and exact commit/dirty-state; changed paths; dependency evidence; tests actually run and results; actual platform evidence; unverified assumptions; blockers; security/privacy changes; next eligible module. Specified tests, generated stubs and documentation do not count as executed proof. Never put implementation handoffs into this source specification folder.
