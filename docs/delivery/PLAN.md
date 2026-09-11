# Delivery plan

Progress record for the MASTER-BUILD-AND-MERGE delivery loop. This is a **record**, not additional
product scope. Requirement and invariant definitions are authoritative in
`specs/accessforge/CONTRACTS.md`; the dependency graph is authoritative in
`specs/accessforge/prompts/README.md`.

Status is tracked in four independent fields, because a merged foundation is not a delivered feature:

- **Impl** — not started / partial / implemented
- **Verify** — pending / passed / failed / blocked
- **Deliver** — local / draft / open / queued / merged
- **Scope** — E0 / R1 / deferred

## Module ledger

| # | Module | Depends on | Requirements | Impl | Verify | Deliver | Scope |
|---|---|---|---|---|---|---|---|
| 00 | Repository and runtime capability gate | — | FR-004, 008, 022, 024 | implemented | passed | merged | E0 |
| 01 | Workspace foundation, pinned runtimes, first CI | 00 | FR-016, 022, 024 | implemented | passed | merged | E0 |
| 02 | Canonical schemas and deterministic reducers | 01 | FR-002, 003, 007, 015, 016 | implemented | passed | merged | E0 |
| 03 | Human, tenant and service identities | 02 | FR-001, 014, 020 | partial | passed | merged | E0 |
| 04 | Authoritative journal and transactional outbox | 02, 03 | FR-006, 014, 015, 021 | implemented | passed | merged | E0 |
| 05 | Authorized projects and immutable builds | 03, 04 | FR-001, 002, 010, 014 | implemented | passed | merged | E0 |
| 06 | Versioned journey DSL, fixtures, assertions | 02, 05 | FR-003, 005, 007, 023 | implemented | passed | merged | E0 |
| 07 | Runner enrollment, admission, desktop leases | 04, 06 | FR-004, 005, 014, 015, 021 | implemented | passed | merged | E0 |
| 08 | Actual macOS VoiceOver execution | 00, 07 | FR-004, 005, 006, 015 | contract only | **blocked — no real reader** | merged | E0 |
| 09 | Actual Windows NVDA execution | 00, 07 | FR-004, 005, 006, 015 | contract only | **blocked — no Windows host** | merged | R1 |
| 10 | Provenance-bound evidence ingestion | 04, 07, 08 | FR-006, 014, 015, 020 | implemented | passed (contract, not end-to-end) | merged | E0 |
| 11 | Independent deterministic outcome verifier | 06, 10 | FR-006, 007, 011, 023 | implemented | passed (no real reader trace) | merged | E0 |
| 12 | Real Strands navigator and bounded tools | 07, 08, 11 | FR-005, 008, 015, 021 | not started | **blocked** | local | E0 |
| 13 | Evidence-grounded diagnosis and findings | 11, 12 | FR-009, 012, 023 | not started | **blocked** | local | E0 |
| 14 | Constrained patch proposal and sandbox | 05, 13 | FR-002, 010, 014, 015 | not started | **blocked** | local | E0 |
| 15 | Matched candidate verification | 11, 14 | FR-002, 007, 010, 011 | not started | pending | local | E0 |
| 16 | Human review and finding lifecycle | 03, 15 | FR-009, 011, 012, 014 | implemented | passed (no human reviewer) | merged | E0 |
| 17 | Redacted export and offline verifier | 10, 11, 16 | FR-006, 007, 012, 013, 020 | implemented | passed (synthetic bundles only) | merged | E0 |
| 18 | HTTP API, generated clients and CLI | 05, 06, 07, 15, 16, 17 | FR-001–007, 010–016 | partial (50 `/v1` paths, SSE stream, generated clients, CLI; only modules 14/15/20 routes remain) | passed | merged | E0 |
| 19 | Durable events and bounded schedules | 04, 18 | FR-015, 017, 021 | implemented (SSE stream, snapshot, schedule and grant routes added by module 18 completion) | passed (no real runner) | merged | E0 |
| 20 | Opt-in GitHub checks and publication | 15, 16, 18 | FR-001, 010–012, 014, 018 | not started | pending | local | R1 |
| 21 | Accessible modern UI foundation | 18 | FR-014, 016, 019 | implemented (shell only; screens are 22–24) | passed (no actual screen reader) | merged | E0 |
| 22 | Projects, journeys and runner UI | 19, 21 | FR-001–005, 015, 017, 019 | implemented (5 screens; no schedules, grants or live-events UI) | passed for what exists; a run can now be requested (sealing route added) but **no run can execute** | merged | E0 |
| 23 | Run and evidence replay UI | 10, 11, 19, 21 | FR-006, 007, 009, 015, 017, 019 | implemented (run, timeline, finding; the SSE stream exists and the UI does not consume it; no media) | **blocked — no per-assertion results are served, and no run has executed** | merged | E0 |
| 24 | Patch, comparison and review UI | 15, 16, 17, 21 | FR-010–013, 019, 020 | implemented (review, export; repair workspace states its absence) | passed for export; **blocked — no patch or verification can exist** | merged | E0 |
| 25 | Fault laboratory and benchmarks | 12, 15, 22, 23, 24; 09 for R1 | FR-004–013, 015, 019, 023 | not started | pending | local | R1 |
| 26 | Security, privacy, metering and operations | 17, 19, 25 | FR-014, 015, 019–021, 025 | implemented (entitlements, metering, retention, settings, probes; no deletion route, telemetry or rate limits) | passed for what is enforced; **no external penetration test** | merged | R1 |
| 27 | CI, deployment preparation and restore | 26 | FR-004, 014, 015, 020–022, 025 | implemented (doctor, migrator, encrypted backup/restore, reconciliation, readiness, trusted/untrusted CI split, AWS proposal) | passed; real backup + restore + reconcile drill executed | merged; **hosted PREPARED, never applied** | R1 |
| 28 | Release proof, documentation and pilot | 27; 09 and 20 for R1 | FR-001–025 | not started | pending | local | R1 |
| 29 | Independent adversarial final review | 28 | FR-001–025 | not started | pending | local | R1 |

## Owned paths

Module 03 owns `packages/domain/src/accessforge_domain/authorization/`, `packages/persistence/`,
`apps/api/src/accessforge_api/auth/` and `docs/adr/0004-*`. Its implementation status is **partial**
by design: the authorization primitives are complete and tested, but no HTTP surface exposes them
because module 18 owns the API.

Module 00 owns `docs/capabilities.md`, `docs/adr/0001-implementation-environment.md`,
`docs/handoffs/00.md`. Module 01 owns the workspace manifests and lockfiles, `apps/api`,
`apps/web`, `apps/desktop-runner`, `fixtures/reference-app`, `tests/`, `.github/workflows/ci.yml`,
`docs/adr/0002-*` and `docs/development/VERIFICATION.md`. The delivery records (`docs/delivery/`, `docs/development/VERIFICATION.md`)
are coordinator-owned per MASTER-BUILD-AND-MERGE §3 and were created alongside module 00's delivery
so the record is honest from the first merge; they contain no product scope. Later modules declare
their own owned paths in their handoffs.

## Sequencing

Module 21 added the three session routes the shell needs (`POST /v1/sessions`, `GET /v1/session`,
`DELETE /v1/session`) rather than leaving the UI unable to establish who is signed in. It did **not**
add a credential store: `ACCESSFORGE_IDENTITY_PROVIDER` defaults to `none`, sign-in then refuses
with a named missing dependency, and the only implemented provider is a local-development bridge
that `ApiSettings` refuses to start outside a `local` environment. See `docs/handoffs/21.md`.

Modules 01–07 are the largely reachable foundation, but the line is not clean at 05. Modules 01–04,
06 and 07 need no screen reader, model endpoint, or target application and can proceed honestly
from the current environment.

**Module 05 is the exception.** Authorized projects and immutable build manifests are target-bound:
its schemas, authorization rules, and digest handling can be built and unit-tested, but real
manifests and any target-bound validation stay BLOCKED until an authorized target application
exists. Module 01 builds a local reference application, which may satisfy that need without an
external target — that has to be established by module 05's own evidence, not assumed here.

Module 08 is where the delivery loop meets its first hard external gate. Its implementation can be
written and unit-tested against labelled fakes, but its actual-AT proof stays BLOCKED until the
owner completes VoiceOver setup (`docs/capabilities.md` §5.1). Modules 12–14 similarly stall at
real-model proof without AWS Bedrock access.

Under MASTER-BUILD-AND-MERGE §12, blocked paths stop; independent work continues.

## Delivery boundaries

One cohesive module per pull request, started from current `main` after the predecessor lands.
Stacked PRs are avoided by default. No application changes are authored on `main`. Branch
protection is not configured on this repository — the owner must set it; the agent does not change
repository settings.
