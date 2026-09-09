# Prompt 20 — Explicitly authorized GitHub checks and patch publication

**Dependencies:** 15, 16, 18  
**Requirements:** FR-001, FR-010–FR-012, FR-014, FR-018; INV-03–INV-05, INV-07, INV-08, INV-11, INV-12  
**Owns:** scoped GitHub integration, check rendering and approval-bound publication adapter

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), integration boundaries in [TDD.md](../TDD.md) and [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 20 only.

Objective: Connect AccessForge's evidence to an explicitly selected repository without allowing model output or a successful local test to publish code or create a misleading release check. This module is optional for E0 and required for full R1.

Inspect existing repository authorization, patch digests, review policy and service credentials. Identify the minimum supported GitHub App permissions from current official documentation. Do not install an app, create a remote repository or change branch protection as part of implementation.

Implementation tasks:
1. Bind an installation to exact workspace and allowlisted repositories. Verify installation/repository ownership and current permissions rather than accepting user-supplied repository strings as authority.
2. Keep short-lived publication credentials in the isolated integration service. Do not expose tokens to navigator, repair agent, browser runner or source-build sandbox.
3. Verify webhook signatures against raw bodies, deduplicate deliveries and scope all payload identities. Treat repository text, comments, commit messages and webhook contents as untrusted data.
4. Admit checks against immutable source SHAs, journey/profile digests and current authorization. A moving branch name is display context, not the proof identity.
5. Map outcomes honestly: pending work stays pending; runner unavailable, interrupted, incomplete or unsupported proof must not appear as a successful accessibility check. Explain PASS coverage and human-review limitations in the check details.
6. Prepare a local publication preview containing exact repository, base SHA, action/payload and patch digest where applicable. Checks, comments, branches and PRs are all external writes. Require one exact current payload-bound GITHUB_PUBLISH approval and show the requested change before publication; no standing publication policy is defined.
7. Ensure ExecutionGrant, RUN_EFFECTS, PATCH_APPLY and human ACCEPT are insufficient to authorize GitHub writes. Recheck publication scope, digest, expiry, revocation and revision at the actual outbound call boundary.
8. Handle ambiguous publication responses by discovering an existing operation through its stable identity before any safe retry. Do not create duplicate branches, pull requests or repeated review comments after timeout.
9. Mark stale checks and approvals when the source/patch changes. Do not silently carry verified status to a new commit or edit old evidence to match the branch head.
10. Keep automatic merge and production deployment out of scope. Provide disconnect/revocation handling, deletion-safe local links and an auditable publication record without storing token contents.

Required verification:
Test spoofed webhooks, replayed deliveries, wrong installation, cross-workspace repo IDs, stale SHA, revoked tokens, insufficient app permissions, malformed model-generated patch metadata and timed-out publication. Prove a repair agent cannot reach the publication credential or bypass GITHUB_PUBLISH. Use fixture webhooks for unit tests, then a separately authorized disposable repository for actual write proof only after explicit approval.

Acceptance gate:
Local preview and check computation are real and testable without external writes. Full R1 integration proof additionally requires an actual authorized installation/check/publication round trip; absent approval is BLOCKED, not simulated completion. E0 can declare GitHub publication unavailable without pretending the module shipped.

Handoff:
Write docs/handoffs/20.md with permissions, exact tested mode, read-only versus actual write evidence, commands/results and unresolved external gates. Never publish or merge merely because this prompt exists. Stop after this module.
```
