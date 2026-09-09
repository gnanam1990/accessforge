# Prompt 03 — Identity, tenancy and scoped authority

**Dependencies:** 02. **Requirements:** FR-001, FR-014, FR-020. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), PRD roles, TDD authorization and TEST-PLAN. One owner in E0 does not justify an unauthenticated R1 architecture.

## Copy-paste prompt

```text
Implement AccessForge module 03 only.

Objective: Establish authority at server, database and service boundaries so an agent, browser request or guessed ID cannot acquire another project's capabilities.

Owns: apps/api/auth/, packages/domain/authorization/, identity/membership persistence migrations, authentication integration tests and the authorization matrix. Coordinate persistence interfaces with module 04 rather than duplicating its repository framework.

Required contracts: Use opaque IDs and workspace-scoped composite relationships. Derive authenticated workspace access from current membership plus route context, never a body workspaceId. Preserve approval scopes RUN_EFFECTS, PATCH_APPLY and GITHUB_PUBLISH without merging their powers.

Tasks:
1. Implement the PRD role matrix as executable permission tests. Separate membership administration, project configuration, test execution approval, patch review, evidence reading/export and infrastructure operations; reject unspecified permissions.
2. Choose and document the configured identity provider/session integration. Use secure server sessions, CSRF defenses on browser mutations, session rotation and logout/revocation. Never ship a production bypass accepting an arbitrary user header.
3. Add identity and workspace membership records with composite tenant constraints. Introduce separate restricted database identities or equivalent row-level enforcement where specified; test actual database behavior, not only middleware mocks.
4. Implement scoped principals for orchestrator, builder, supervisor, independent observer and ingestion. Add producer/event-kind ACLs: supervisor cannot emit independent EFFECT_RECEIPT or observer assertions; observer cannot send OS actions. A desktop lease token cannot approve patches or browse unrelated artifacts.
5. Define short-lived enrollment credentials and single-use enrollment redemption. Revoke credentials and device admission independently; do not make a permanent device secret equivalent to an active run authorization.
6. Persist explicit environment-owner authorization and permitted test effects. Expired or revoked permission must fail at dispatch even when a previously accepted request exists.
7. Add exact-digest/revision approvals with attribution, expiry and revocation. Define explicit ExecutionGrant authority and trusted issuance of exact RUN_EFFECTS children with parent ID/revision and issuing identity. Both minting and dispatch recheck parent scope; grants never authorize patches/publication. Show scope without secrets.
8. Protect support/admin operations with explicit audit events and minimum scope. Do not implement an undocumented cross-tenant impersonation route or trust a client-supplied administrator flag.
9. Define privacy-safe audit fields for membership, session and approval changes. Avoid recording passwords, bearer tokens, reader transcripts or unnecessary demographic information in identity logs.
10. Provide E0 owner-operated setup that uses the same authorization checks. Development fixtures may create test identities explicitly; no hidden default credentials or implicit production bootstrap.

Negative tests: Cross-workspace substitution, stale membership during replay, CSRF, expired/reused token, forged role, device token as user session, supervisor-forged receipt, observer-requested OS action, revoked grant after child issuance, unprivileged child minting and body/path workspace mismatch (INV-07, INV-08). Test direct restricted database access and service-to-service calls to expose bypasses below the UI.

Acceptance: Each role has allowed and denied examples against real integration boundaries. Removing membership or revoking authority blocks subsequent access and dispatch; opaque IDs alone are not the isolation mechanism. Unimplemented downstream resource surfaces remain explicitly pending.

Stop conditions: If existing identity setup cannot supply secure sessions or tenant binding, block the affected public surface. Do not expose an unauthenticated server while calling it an owner-only product.

Handoff: Write docs/handoffs/03.md with the executable permission matrix, token lifecycle evidence, database isolation results, secret-handling changes and contracts consumed by modules 04 and 05.
```
