# ADR 0004 — Tenant isolation mechanism and role-matrix interpretation

- **Status:** Accepted
- **Date:** 2026-09-10
- **Module:** 03 (identity, tenancy and scoped authority)
- **Builds on:** [ADR 0003](0003-canonicalization-and-contract-generation.md)

## Context

Module 03 had to make cross-workspace access impossible rather than merely unlikely, and had to turn
four prose role descriptions into an executable matrix. Three decisions needed recording.

## Decision 1 — FORCE ROW LEVEL SECURITY, not a second database role

Workspace-scoped tables use PostgreSQL row-level security with `FORCE`, and the application
establishes its scope per transaction with `set_config('accessforge.workspace_id', …, true)`.

The obvious alternative is a separate restricted login role that is not the table owner. That is
stronger in principle, but creating roles needs privileges the application's own role does not have,
which would push isolation into a manual setup step nobody can test from the suite — and an
untestable control is not a control.

`FORCE` was chosen because without it **the table owner bypasses every policy**, and in local
development the application connects as the owner. Plain `ENABLE` would have made every isolation
test pass while proving nothing. A test asserts `relforcerowsecurity` on all five protected tables,
and a second test removes `FORCE` inside a rolled-back transaction to confirm both workspaces then
leak — so the suite demonstrates it is measuring the thing it claims to.

The scope is established with `SET LOCAL` semantics inside the transaction, so it cannot survive
into the next user of a pooled connection. A leaked scope would be worse than none: it would widen
access silently rather than deny it.

An unset or malformed scope resolves to `NULL`, which matches no row. Failing closed here is the
whole point — a forgotten scope must mean "nothing", never "everything".

**Accepted consequence:** a separate restricted role remains worthwhile defence in depth for R1 and
belongs to module 26's hardening, where infrastructure provisioning is in scope.

## Decision 2 — the role matrix, including where the specification was ambiguous

SECURITY-PRIVACY section 3 describes owners, maintainers, reviewers and viewers in prose. Turning
that into permissions required two judgements, recorded here because they are interpretations rather
than quotations:

**Maintainers may approve runs and patches.** The text says they "configure authorized projects and
request approved work", which could also be read as request-only. Approval was granted because
`PATCH_APPLY` authorizes an isolated candidate workspace and nothing else, and requiring an owner for
every run in a maintainer's own project would make the role unusable. `GITHUB_PUBLISH_APPROVE` was
deliberately **not** granted: publication leaves the system, and FR-018 makes it a separate
permission from attaching a repository.

**Maintainers may not record review verdicts.** `PATCH_REVIEW` is reviewer-only. The specification is
explicit that reviewing confers no execution authority; the converse — that approving work should not
also let you sign off on it — follows from the same separation and is the more conservative reading.

Roles are **not a hierarchy**. `REVIEWER` is not a subset of `MAINTAINER`, and the matrix is written
out per role rather than derived by inheritance. Inheritance is how a reviewer quietly acquires
execution authority when someone adds a permission to the wrong tier. A test asserts the
non-inclusion directly.

The expected matrix is duplicated in the test file on purpose. Changing it requires editing two
places, one of which a reviewer reads.

## Decision 3 — machine principals hold no role permissions at all

`MachinePrincipal.permits()` returns `False` for every permission, unconditionally. Service
identities are a separate type from human principals, so a desktop lease credential cannot be passed
where a session is expected.

Anything a service may do is an explicit capability check — the event-producer ACL, the
operating-system dispatch check — never a role lookup that happens to succeed. FR-014 requires that
service credentials not inherit administrator rights by convenience, and the cheapest way to
guarantee that is to give them no role to inherit from.

The event-producer ACL enforces the separation the evidence model depends on: the supervisor drives
the journey and may submit reader, action, preflight and lifecycle records; the independent observer
reads application state and may submit receipts and observer assertions. The two sets are disjoint,
asserted at import time, and every event kind has exactly one permitted producer — a kind with two
has no separation, and a kind with none is dead contract surface.

## Consequences

Every workspace-scoped query now needs a scoped connection. `unscoped_connection` exists for
genuinely workspace-independent work and is deliberately named unattractively so that reaching for it
is a visible decision in review.

Authorization is re-derived per request from live membership. A long-lived session whose membership is
revoked stops working immediately, which is tested rather than assumed.

Migrations run through a minimal runner in `packages/persistence`. Module 04 owns the repository
framework and should build on these primitives rather than introduce a second connection discipline.

## Rejected alternatives

- **Predicates in application code only.** The threat is a SQL path that forgets its predicate — a
  new query, a report, a migration script. Application-level filtering cannot help with any of them.
- **Opaque identifiers as the isolation mechanism.** Unguessable ids raise the cost of an attack and
  prove nothing; the acceptance criteria say so explicitly.
- **One service account for all machine identities.** Would make supervisor and observer
  interchangeable, which would remove the separation that makes a run outcome mean anything.
