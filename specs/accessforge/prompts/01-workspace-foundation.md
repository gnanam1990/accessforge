# Prompt 01 — Reproducible workspace foundation

**Dependencies:** 00. **Requirements:** FR-016, FR-022, FR-024. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), PRD, TDD and TEST-PLAN. The paths below describe the future implementation checkout, not files already delivered in this specification pack.

## Copy-paste prompt

```text
Implement AccessForge module 01 only.

Objective: Create a reproducible Python/TypeScript workspace with honest health checks, a real reference application and independent test commands. Do not implement later modules behind placeholder success responses.

Owns: root workspace manifests and lockfiles, development tooling, CI skeleton, app/package entrypoints, fixtures/reference-app/, and foundational setup documentation. Domain schemas remain owned by module 02; database business logic belongs to module 04.

Required inputs: Read docs/handoffs/00.md and capability evidence. Use Python/FastAPI and Strands workers, TypeScript desktop adapters, React/TypeScript UI, PostgreSQL and private S3-compatible storage as chosen in CONTRACTS.md. Pin an actually tested version set using uv and pnpm lockfiles.

Tasks:
1. Inspect the real checkout and preserve existing work. Record an ADR if an existing framework conflicts with the chosen stack; do not silently substitute a different architecture.
2. Establish the specified apps/, packages/, fixtures/, tests/, infra/ and docs/handoffs/ boundaries. Prevent apps importing another app's private implementation through lint/import rules.
3. Configure typed Python and TypeScript builds, formatting, static checks and independently runnable test suites. Generate no misleading all-green tests that merely assert a constant.
4. Add development PostgreSQL and private object-store configuration with explicit ports, persistent development volumes and safe task-specific names. Do not repurpose existing databases or delete broad directories during reset.
5. Create documented .env.example files containing placeholders only. Validate required configuration, environment identity and production-safe defaults at startup; redact sensitive values from diagnostics.
6. Implement liveness and readiness as different concerns. A missing database or evidence store must make dependency readiness visibly fail; an HTTP process responding does not mean the product is usable.
7. Build a small genuinely functioning form application and backend with deterministic seeded test identities, server-side validation, durable submission receipts and a narrowly authorized test-reset interface.
8. Provide a documented inaccessible fixture variant and a reference accessible behavior expectation without pretending the seeded defect is a discovered customer incident. Do not bake candidate answers into navigator input or expose receipt credentials to browser code.
9. Keep fixture submissions restricted to local/owned test infrastructure. Add hard safeguards against real email, payment, production endpoint or external application submission.
10. Document clean installation, startup, shutdown, health inspection and test commands. Execute them in a clean task-specific directory or environment when available; otherwise record the missing proof.

Negative tests: Invalid environment configuration fails closed; a frontend-only form cannot satisfy a receipt test; persistence survives backend restart; unauthorized reset fails; dependency outage is not readiness success; secrets never appear in application exceptions. Test the difference between a functional API receipt and an accessibility claim (INV-02, INV-03, INV-08).

Acceptance: A fresh installation starts real local services, the fixture accepts and persists an authorized test submission, restart preserves it, and commands produce observed results. Screen-reader accessibility is explicitly NOT YET VERIFIED until module 08 and downstream evaluation run.

Stop conditions: Report incompatible runtimes, missing package access or unsafe existing-service overlap. Continue independent tooling work but do not substitute ephemeral storage or fabricated integration health.

Handoff: Write docs/handoffs/01.md with exact versions, command results, changed paths, local service identities without secrets and remaining blockers. Module 02 may proceed only against the established workspace contract.
```

