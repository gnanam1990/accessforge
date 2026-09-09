# Claude Code implementation prompt pack — AccessForge

30 bounded implementation prompts (`00`–`29`) and one shared session header. They are a build sequence, not evidence that any module is already implemented. The product, wire contracts, technical design and test-driven acceptance live in separate files so implementation sessions can stay focused without losing the end-to-end intent.

**Full-project delivery mode:** the owner can explicitly activate [MASTER-BUILD-AND-MERGE.md](../MASTER-BUILD-AND-MERGE.md). It coordinates this same sequence through tested commits, PR review, guarded merges and main verification, then continues to the next eligible module. Only its explicitly authorized orchestration/implementation-repository actions override the default one-module stop. All product contracts and external-effect boundaries remain intact.

## Start here

1. Choose the actual implementation checkout in Prompt 00. Inspect its instructions, current files, exact HEAD, dirty worktree, dependencies and licenses. Do not assume this specification directory is that checkout.
2. Copy the **whole AccessForge folder**, preserving its internal relative links, into that checkout as `specs/accessforge/`. Alternatively provide its actual absolute path. Do not copy only a prompt and omit its contracts or test plan.
3. Load [SESSION-HEADER.md](SESSION-HEADER.md) in every session, then the chosen numbered prompt's complete copy-paste block. Read the referenced specifications and actual dependency handoffs before editing.
4. Begin with **Prompt 00 only**. Do not paste all 30 modules into one uncontrolled request or allow a dependency file's existence to stand in for completed implementation.
5. Each implementation session writes `docs/handoffs/NN.md` in the implementation checkout. Keep these runtime handoffs out of this source specification folder.
6. Resolve contradictory contracts explicitly and update the affected PRD/CONTRACTS/TDD/TEST-PLAN/prompts together before proceeding. Never weaken a safety or evidence condition to make a dependency appear complete.

Initial instruction to Claude Code:

```text
Read specs/accessforge/README.md and specs/accessforge/prompts/SESSION-HEADER.md.
Then read and execute specs/accessforge/prompts/00-capability-gate.md only.
Inspect the actual checkout before editing. Treat this specification as proposed design, not existing code.
Check the real VoiceOver/Strands/reference-application capability requirements without performing unapproved external actions.
Produce docs/handoffs/00.md with evidence, exact capability mode and blockers, then stop.
Do not continue into Prompt 01, create cloud resources, publish code or submit an event entry automatically.
```

For later sessions, select the next eligible numbered file and reload the header. A module can be implemented while its real-platform proof remains blocked; the handoff must distinguish those states. Stubbed success cannot clear the block.

## Dependency and coverage index

Dependencies refer to **actual completed work and required proof**, not merely numbered documents. Requirement/invariant definitions are authoritative in [CONTRACTS.md](../CONTRACTS.md).

| Module | Required dependencies | Main requirement coverage |
|---|---|---|
| [00 — Repository and runtime capability gate](00-capability-gate.md) | None | FR-004, FR-008, FR-022, FR-024 |
| [01 — Workspace foundation and pinned runtime contracts](01-workspace-foundation.md) | 00 | FR-016, FR-022, FR-024 |
| [02 — Canonical schemas and deterministic reducers](02-contracts-and-reducers.md) | 01 | FR-002, FR-003, FR-007, FR-015, FR-016 |
| [03 — Human, tenant and service identities](03-identity-and-tenancy.md) | 02 | FR-001, FR-014, FR-020 |
| [04 — Authoritative journal and transactional outbox](04-journal-and-outbox.md) | 02, 03 | FR-006, FR-014, FR-015, FR-021 |
| [05 — Authorized projects and immutable builds](05-projects-and-build-manifests.md) | 03, 04 | FR-001, FR-002, FR-010, FR-014 |
| [06 — Versioned journey DSL, fixtures and assertions](06-journey-dsl-and-assertions.md) | 02, 05 | FR-003, FR-005, FR-007, FR-023 |
| [07 — Runner enrollment, admission and desktop leases](07-runner-control-plane.md) | 04, 06 | FR-004, FR-005, FR-014, FR-015, FR-021 |
| [08 — Actual macOS VoiceOver execution](08-macos-voiceover-runner.md) | 00, 07 | FR-004, FR-005, FR-006, FR-015 |
| [09 — Actual Windows NVDA execution](09-windows-nvda-runner.md) | 00, 07; required R1 | FR-004, FR-005, FR-006, FR-015 |
| [10 — Provenance-bound evidence ingestion](10-evidence-ingestion.md) | 04, 07, 08 | FR-006, FR-014, FR-015, FR-020 |
| [11 — Independent deterministic outcome verifier](11-outcome-verifier.md) | 06, 10 | FR-006, FR-007, FR-011, FR-023 |
| [12 — Real Strands navigator and bounded tools](12-strands-navigator.md) | 07, 08, 11 | FR-005, FR-008, FR-015, FR-021 |
| [13 — Evidence-grounded diagnosis and findings](13-evidence-diagnosis.md) | 11, 12 | FR-009, FR-012, FR-023 |
| [14 — Constrained patch proposal and sandbox](14-patch-sandbox.md) | 05, 13 | FR-002, FR-010, FR-014, FR-015 |
| [15 — Matched candidate verification](15-candidate-verification.md) | 11, 14 | FR-002, FR-007, FR-010, FR-011 |
| [16 — Human review and finding lifecycle](16-human-review.md) | 03, 15 | FR-009, FR-011, FR-012, FR-014 |
| [17 — Redacted export and offline verifier](17-evidence-export.md) | 10, 11, 16 | FR-006, FR-007, FR-012, FR-013, FR-020 |
| [18 — HTTP API, generated clients and CLI](18-http-api-and-clients.md) | 05, 06, 07, 15, 16, 17 | FR-001–FR-007, FR-010–FR-016 |
| [19 — Durable events and bounded schedules](19-events-and-schedules.md) | 04, 18 | FR-015, FR-017, FR-021 |
| [20 — Opt-in GitHub checks and publication](20-github-integration.md) | 15, 16, 18; required R1 | FR-001, FR-010–FR-012, FR-014, FR-018 |
| [21 — Accessible modern UI foundation](21-ui-foundation.md) | 18 | FR-014, FR-016, FR-019 |
| [22 — Projects, journeys and runner UI](22-ui-projects-journeys-runners.md) | 19, 21 | FR-001–FR-005, FR-015, FR-017, FR-019 |
| [23 — Run and evidence replay UI](23-ui-run-replay.md) | 10, 11, 19, 21 | FR-006, FR-007, FR-009, FR-015, FR-017, FR-019 |
| [24 — Patch, comparison and review UI](24-ui-repair-and-review.md) | 15, 16, 17, 21 | FR-010–FR-013, FR-019, FR-020 |
| [25 — Fault laboratory and benchmarks](25-fault-lab-and-benchmarks.md) | 12, 15, 22, 23, 24; 09 for R1 | FR-004–FR-013, FR-015, FR-019, FR-023 |
| [26 — Security, privacy, metering and operations](26-security-privacy-and-operations.md) | 17, 19, 25 | FR-014, FR-015, FR-019–FR-021, FR-025 |
| [27 — CI, deployment preparation and restore](27-ci-deployment-and-recovery.md) | 26 | FR-004, FR-014, FR-015, FR-020–FR-022, FR-025 |
| [28 — Release proof, documentation and pilot](28-release-proof-and-pilot.md) | 27; 09 and 20 for full R1 | FR-001–FR-025 |
| [29 — Independent adversarial final review](29-independent-final-review.md) | 28 | FR-001–FR-025 |

## E0 versus R1

**E0 is a real working event slice**, not a fake product demo. It requires the actual authorized application/backend, pinned VoiceOver profile, real Strands execution, failed baseline, approved candidate build, independent verification, actual human review, usable UI and verifiable export. Module 09 (NVDA) and module 20 (GitHub) may be deferred. Module 19 still provides correct replayable events; scheduling may be manual-only and explicitly unavailable in E0. The E0 local desktop limitation must remain visible.

**R1 is the complete web product.** It requires all 30 modules' applicable work and proof, including actual Windows/NVDA, authorized GitHub integration, schedules, multi-workspace isolation, privacy/retention, usage entitlements and clean installation/recovery. Payment collection and R2 mobile/PDF/native-desktop remediation remain out of scope. Finishing all filenames does not establish R1 if a runtime or external gate is still blocked.

See [IMPLEMENTATION-PLAN.md](../IMPLEMENTATION-PLAN.md) for milestone acceptance. Full product ambition is not reduced to the event slice, and the event slice must not be advertised as the full platform.

## Safe parallel work

Numbered order is the safe default. Once their real dependencies are satisfied, 08/09 may progress separately against one frozen runner protocol; 20/21 may progress separately after their respective dependencies; and 22/23/24 can be divided after the shared API and UI shell are stable. Coordinate shared components and schema changes explicitly; do not let each branch define a different run verdict, approval digest or tenant identity.

Platform gates may block dispatch/proof without blocking pure schema or local test work. Record that distinction rather than silently replacing actual AT with DOM automation. Reconcile branch changes and rerun shared tests before accepting downstream handoffs.

Module 29 is a fresh review. A fix returns to its owning implementation module, invalidates affected old proof, and requires a new review at the updated exact head. There is no automatic merge, publication, deployment, cloud purchase, external outreach or hackathon submission in this prompt pack.
