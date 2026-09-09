# ADR 0002 — Configuration files, workspace boundaries and the reference application

- **Status:** Accepted
- **Date:** 2026-09-09
- **Module:** 01 (reproducible workspace foundation)
- **Builds on:** [ADR 0001](0001-implementation-environment.md)

## Context

Module 01 had to turn an empty repository into a workspace that can host the stack CONTRACTS §2
specifies, and give AccessForge something real to test against. Three decisions were not obvious.

## Decision 1 — one environment file per settings consumer

`.env` (control plane), `.env.refapp` (reference application) and `.env.test` (test harness) are
separate files, each read by exactly one consumer.

The obvious design is a single `.env`. It was tried and rejected: every settings class uses
`extra="forbid"` so that a typo like `REFAPP_PORTT` fails startup instead of silently leaving the
default in place, and pydantic-settings passes *all* dotenv keys to *every* settings class. With
one shared file, each class saw the other prefixes as unknown keys and refused to start.

The alternative was relaxing to `extra="ignore"`, which would have restored the silent-typo
failure mode that task 5 exists to prevent. Splitting the files keeps the stricter guarantee.

## Decision 2 — the reference application is a real application, not a mock

`fixtures/reference-app/` is a working FastAPI service with PostgreSQL persistence, server-side
validation, durable receipts, and separate observer and setup identities. It is not a static page
and not a stubbed backend.

The seeded accessibility defect is deliberately **presentational only**. Both variants run
identical server-side validation and produce identical success pages; they differ solely in
whether errors are announced (`role="alert"`, `aria-invalid`, `aria-describedby`, focus movement).
This matters for verification integrity: a candidate repair must fix how the failure is exposed to
assistive technology, and must not be able to earn a pass by loosening validation. `tests/unit/
test_fixture_variants.py` fails if the seeded defect silently heals, and the mutation check
confirms that test actually catches it.

The success page is byte-identical across variants on purpose. It encodes INV-02 structurally: a
receipt carries no accessibility signal, so no downstream code can read one as evidence that the
journey was operable.

**One request per fixture instance is enforced by a unique index**, not by application logic. The
journey's success condition is "exactly one request", so a resubmission surfaces as a 409 rather
than a second silent receipt.

## Decision 3 — empty directories carry ownership markers, not placeholders

`packages/*`, `apps/orchestrator`, `apps/build-worker` and `infra/` contain a README naming the
owning module and stating that nothing is implemented. `apps/desktop-runner` compiles and exits
**78 (EX_CONFIG)** with a message naming modules 07 and 08.

A stub returning success would be indistinguishable from a working runner to anything downstream.
Exiting non-zero makes absence unmistakable while still giving the workspace a real build target.

## Consequences

Local development needs three env files rather than one, and `.gitignore` uses `!.env*.example` so
a newly added template cannot be silently excluded — the individually-listed pattern had already
swallowed two.

The API's `/health/ready` returns **503** in normal local development because no S3-compatible
store is running. That is intended: a missing evidence store must be visible, not smoothed into a
200 or replaced with ephemeral storage.

CI covers `pull_request`, `push` to main, and `merge_group`, because a merge queue validates a
commit that neither branch contains.

## Rejected alternatives

- **SQLite for tests.** Faster, but PostgreSQL is authoritative per CONTRACTS, and the constraints
  being relied on (unique index behaviour, transactional semantics) are the point.
- **A static HTML fixture.** Cannot produce a durable receipt, so it could not distinguish a real
  task completion from a rendered success page — the exact confusion the observer exists to resolve.
- **Deferring CI to module 27.** MASTER-BUILD-AND-MERGE §5 requires meaningful CI from module 01;
  module 27 hardens a pipeline rather than inventing the first trustworthy one.
