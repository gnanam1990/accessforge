# Prompt 18 — Validated HTTP API, generated clients and operator CLI

**Dependencies:** 05, 06, 07, 15, 16, 17  
**Requirements:** FR-001–FR-007, FR-010–FR-016; INV-03, INV-07, INV-08, INV-11, INV-12  
**Owns:** public FastAPI routes, OpenAPI, generated clients and bounded operator CLI

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [TDD.md](../TDD.md) and API acceptance cases in [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 18 only.

Objective: Make existing domain capabilities usable through one authenticated, versioned interface. Do not rebuild state reducers in route handlers or silently introduce authority that the domain does not possess.

Inspect all completed dependency services, actual schemas and route conventions. Produce a route-to-domain-service map before editing. The specification describes desired coverage; an OpenAPI document or generated stub does not prove a route works.

Implementation tasks:
1. Implement /v1 resources for authorized projects/environments, versioned journeys, runner status/enrollment, runs and evidence, findings, patch approvals, verification, human reviews and exports. Match the precise TDD route inventory.
2. Derive workspace access from authenticated membership plus requested path. Do not trust workspaceId in a body or reuse an earlier cached allow decision after membership revocation.
3. Validate input/output against authoritative JSON Schema-derived contracts. Reject unexpected security-sensitive fields, overflow, invalid enums, malformed digests and ambiguous timestamp/identity representations.
4. Apply Idempotency-Key consistently to mutations. Same principal/workspace/route/key and same canonical body replays the accepted operation; changed body returns 409. Recheck read authorization before replaying response data.
5. Require If-Match for revision-sensitive mutation. Return explicit RFC7807-style problem details with safe code/requestId, appropriate 400/401/403/404/409/422/429/503 semantics and no secret-bearing stack traces.
6. Return 202 with operation/run identity for asynchronous work. Cancellation request returns request metadata, not a stopped claim; expose stop acknowledgement separately. Completed runs can FAIL or be INCONCLUSIVE. Evidence ingestion accepts authenticated typed source records, never caller-selected canonical sequence/hash or supervisor-forged observer receipts.
7. Generate Python/TypeScript clients from the checked contract and add a CLI for inspect, plan, authorized run request, cancellation, evidence export and offline verification. Dangerous effects require explicit identifiers and displayed scope; no generic shell/OS execution endpoint.
8. Add cursor pagination and stable ordering for history. Avoid sensitive identifiers or existence leaks in cross-tenant errors. Bound upload sizes, page sizes and request duration.
9. Document browser secure-session/CSRF behavior separately from short-lived workspace-bound runner credentials. Do not allow an enrollment token to function as a general administrator API token.
10. Keep HTTP retry policy explicit. Retry safe reads within limits; never blindly repeat consequential OS/application actions. Client operation polling must preserve pending, interrupted and unknown evidence states.

Required verification:
Run real API plus PostgreSQL tests for every published route family and generated-client compatibility. Include unauthenticated access, forged workspace, stale revision, changed idempotent body, revoked membership on replay, pagination crossing workspaces, unknown field, malformed digest, 202 without completion and unavailable storage/runner. Test CLI errors and exit codes without logging secrets. Generate-and-diff contracts in CI rather than trusting checked-in output.

Acceptance gate:
A clean client can create a valid journey, request an explicitly authorized local test run, inspect its actual state and retrieve an export through the real API. Missing upstream runtime proof remains BLOCKED, not substituted by a mocked server. No external integration is published by this module.

Handoff:
Write docs/handoffs/18.md with route coverage, actual commands/results, generated artifact checks and unresolved API/runtime gaps. Stop after this module.
```
