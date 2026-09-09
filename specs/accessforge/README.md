# AccessForge — complete product specification and build prompts

Version 1.0 · 9 September 2026 · **Documentation package; application not yet implemented by this package.**

Delivery-workflow addendum: [MASTER-BUILD-AND-MERGE.md](MASTER-BUILD-AND-MERGE.md) now provides the owner-selected full build → verify → review → PR → main merge → post-merge workflow. Use its activation prompt when you want Claude to coordinate the entire project, rather than stop after each module. Repository authority must be explicitly scoped; reading a file does not grant it.

**AccessForge helps engineers reproduce a web accessibility barrier with a real screen reader, prepare a constrained source repair, independently rerun the journey, and review/export exactly what was established.** It is not an accessibility overlay, generic chatbot or legal-compliance certificate.

## Start here

இந்த package-ல் problem, full product scope, technical architecture, modern UI, testing, security, deployment மற்றும் Claude Code-க்கு module-wise build prompts தனித்தனியாக இருக்கின்றன. Documents English-ல் இருப்பதால் implementation-க்கு நேரடியாகப் பயன்படுத்தலாம். **முதலில் Prompt 00 மட்டும் தொடங்குங்கள்**—actual screen-reader capability gate pass ஆன பிறகே அடுத்த modules.

| File | Purpose |
|---|---|
| [MASTER-BUILD-AND-MERGE.md](MASTER-BUILD-AND-MERGE.md) | End-to-end Claude delivery instructions: repo/worktrees, meaningful CI, real runs, commits, independent review, PRs, guarded merges and main verification. |
| [PRD.md](PRD.md) | Problem, users, product scope, 25 requirement families, detailed acceptance and business-validation gates. |
| [TDD.md](TDD.md) | Technical Design Document: architecture, storage, APIs, agents, runner, patch, evidence, deployment and recovery. |
| [CONTRACTS.md](CONTRACTS.md) | Canonical identities, states, wire conventions, authority and 16 invariants shared by every module. |
| [TEST-PLAN.md](TEST-PLAN.md) | 80 named acceptance/adversarial scenarios, proof layers, traceability and test-driven execution. |
| [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md) | Dependency graph, vertical release slices, execution order and stop/go gates. |
| [UI-UX.md](UI-UX.md) | Design tokens, routes, replay/diff/review screens, full operational states and actual accessibility verification. |
| [SECURITY-PRIVACY.md](SECURITY-PRIVACY.md) | Threat model, credentials, desktop/build isolation, consent, retention, abuse and incident response. |
| [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) | Evidence required for working release, event artifacts, pilot and final review. |
| [SOURCES.md](SOURCES.md) | Official event/technical sources, competitor evidence and unvalidated claims. |
| [prompts/README.md](prompts/README.md) | Index and dependency order for 30 separate Claude Code prompts, numbered 00–29. |
| [prompts/SESSION-HEADER.md](prompts/SESSION-HEADER.md) | Shared implementation rules to use before each module. |

TDD above means Technical Design Document; test-driven development is specified separately in TEST-PLAN. Every named scenario is a **test specification**, not a claim that a test suite has run.

## How to use with Claude Code

1. Choose a new implementation checkout or an explicitly approved existing repository. Inspect its instructions and dirty state; do not overwrite an existing product.
2. Copy this entire folder into that checkout as `specs/accessforge/`. Preserve the relative document structure. This source pack itself is not the application repository.
3. Read the PRD, CONTRACTS and TDD. Give Claude Code [SESSION-HEADER](prompts/SESSION-HEADER.md), then [Prompt 00](prompts/00-capability-gate.md). Allow it to prove the actual reader/browser/OS path on a dedicated authorized desktop.
4. If the gate passes, execute the next eligible prompt according to the dependency index. Do not paste all 30 prompts as an uncontrolled instruction batch. Parallel work requires non-overlapping owned paths and completed dependencies.
5. Require real tests and a numbered `docs/handoffs/NN.md` in the implementation checkout. Review blockers, exact source state and actual platform evidence before advancing.
6. Finish the release and independent-review gates. Publishing, deploying, submitting, accepting terms or granting new consequential authority require explicit approval; a prompt file is not that approval.

For controlled full-project execution, use the [master activation prompt](MASTER-BUILD-AND-MERGE.md) instead of the one-module stopping behavior above. It still starts at Prompt 00, but continues after each evidence-backed delivery gate. Optional exact-repository GitHub authorization covers qualifying implementation PRs only; product approvals, deployment, cloud spend and event submission remain separate.

## Full ambition versus first working release

- **E0:** complete working vertical slice—one authorized real-backend app, actual VoiceOver, Strands, reproduced form-error recovery failure, constrained repair, independent candidate verification, human review and export. A local dedicated desktop is allowed with disclosed limitations.
- **R1:** full web product—workspaces, actual VoiceOver and NVDA profiles, durable operation, UI/API/CLI, schedules, opt-in GitHub integration, privacy, usage entitlements, deployment and recovery.
- **R2+:** mobile/native/PDF and managed desktop-fleet expansion after new capability/security designs. Not hidden R1 commitments.

The user's request for a real product is reflected in real backend effects, actual AT evidence and explicit failure/recovery gates. E0 is not permission to fake those. Conversely, an event deadline does not justify claiming the entire R1 roadmap was delivered.

## Honest status and constraints

These are proposed specifications and implementation prompts. No app, cloud deployment, GitHub publication, external submission, paid pilot, legal certification or actual screen-reader test was completed by writing them. Demand and differentiation still need validation; current competitor overlap is recorded in SOURCES. The UI skill influenced concrete accessibility/error/replay requirements, while the hackathon-research skill kept event scope and evidence claims separate.

Read [SOURCES.md](SOURCES.md) for the verified event date and recheck before submission. Use the release checklist to assess proof, not the size of this package.
