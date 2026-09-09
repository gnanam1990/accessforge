# Prompt 00 — Implementation checkout and capability gate

**Dependencies:** None. **Requirements:** FR-004, FR-008, FR-022, FR-024. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), PRD, TDD and TEST-PLAN before running this prompt. This is a proposed implementation instruction, not evidence that AccessForge exists.

## Copy-paste prompt

```text
Implement AccessForge module 00: establish which real implementation path is available before making promises about it.

Objective: Select and inspect the actual implementation checkout, then produce an evidence-backed capability matrix. Do not create application code in the supplied specification directory.

Owns: docs/capabilities.md, docs/adr/0001-implementation-environment.md, and docs/handoffs/00.md in the selected implementation checkout. Read-only discovery elsewhere requires task-relevant scope; do not examine unrelated credentials or account data.

Required contracts: Preserve E0 versus R1 boundaries from CONTRACTS.md. E0 requires one real macOS/VoiceOver combination; R1 additionally requires an independently proven Windows/NVDA combination. A virtual screen reader, accessibility tree dump or prerecorded transcript is not that capability.

Tasks:
1. Resolve the implementation directory from the user's explicit selection. If none is supplied, ask for the destination before initializing a repository. Inspect applicable AGENTS.md/CLAUDE.md, git status and HEAD; preserve all existing changes.
2. Record operating system, CPU architecture, supported Python/Node runtimes and package managers without claiming an untested dependency version is supported.
3. Inspect current official Guidepup documentation and identify the exact reader, browser, interactive desktop and permissions needed. Record source URLs and retrieval date; do not invent adapter methods.
4. Detect available macOS/VoiceOver capability without toggling Accessibility permissions, installing software or disrupting the user's desktop without permission. Document a dedicated test session requirement and safe stop procedure.
5. If authorized and already configured, run a minimal actual-reader smoke probe against an owned test page. Preserve actual observations and exact versions. Distinguish attempted, unavailable, failed and successfully observed capability.
6. Record Windows/NVDA separately. Its absence blocks that R1 profile, not honest E0 foundation work. Do not emulate Windows behavior on macOS and label it tested.
7. Check local PostgreSQL and S3-compatible development options. Do not silently fall back to in-memory persistence for business-state integration proof.
8. Inspect Strands and Bedrock prerequisites without printing credentials. Model invocation, AWS provisioning and billable resource creation need explicit configured authority; record unavailable model access as a blocker, not a reason to fabricate a response.
9. Inventory the authorized target application, real backend, seeded test accounts and permitted effects. Public reachability is not permission to automate purchases, submissions or account creation.
10. Write GO/PARTIAL/BLOCKED results per capability, remediation steps, estimated verification effort and the modules each missing capability actually blocks.

Negative verification: Demonstrate that Linux-only, virtual-reader-only, absent required Accessibility permission, unknown browser version and missing model access cannot be reported as real E0 proof (INV-02, INV-03). A local credential file existing is not successful service access. Never discover secrets by dumping environment variables.

Acceptance: A reader can identify the exact implementation location, available real boundaries, missing permissions and admissible next work. Capability status includes commands actually executed and observed results, not a checklist of intended commands.

Stop conditions: Stop an affected probe when it requires new authority or risks an active desktop. Continue independent read-only inventory; never relax E0/R1 claims to make the gate green.

Handoff: Write docs/handoffs/00.md with the canonical handoff fields. State whether module 01 may begin and which actual-run acceptance gates remain blocked. No deployment, paid invocation or event submission is authorized by this prompt alone.
```
