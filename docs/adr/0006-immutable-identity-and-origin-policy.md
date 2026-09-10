# ADR 0006 — Immutable identity, origin policy, and one constraint that was wrong

- **Status:** Accepted
- **Date:** 2026-09-10
- **Module:** 05 (authorized projects and immutable build manifests)
- **Builds on:** [ADR 0005](0005-durability-and-the-outbox.md)

## Context

Module 05 connects a project to an authorized source and environment and freezes the exact bytes a
run used. The governing rule is that **a mutable reference is never an identity**: a branch name, an
image tag and a deployment URL can all change while keeping the same text.

## Decision 1 — commit and tree digest captured separately, dirtiness recorded not refused

A commit SHA says where a tree came from; a tree digest says what it contains. They are separate
columns because a dirty checkout shares the first and not the second, and because a force push
changes what a ref means while leaving the ref name intact.

A dirty tree is **recorded, not refused**. Local development is a legitimate E0 case. What is refused
is the *claim*: `assert_reproducible` raises, so a dirty tree can be used while never being described
as clean HEAD. Separating "usable" from "reproducible" is what makes that possible.

Paths are part of the tree digest, so a rename changes identity even with byte-identical content.
Symlinks are recorded by target rather than followed — following one would let a file outside the
tree contribute to its identity.

## Decision 2 — an origin is scheme, host and port, compared after normalization

`HTTP://Example.test:80/app/` and `http://example.test/app` are the same origin. Comparing raw
strings would treat them as different, and an attacker-chosen casing or a default port would slip
past an allowlist.

Path is deliberately **not** part of an origin, so an allowlist entry cannot be narrowed to a path
and then widened by navigating elsewhere on the same host. Membership is exact: no wildcards, no
parent-implies-child, no suffix matching — an approved `staging.example.test` carries no authority
over `example.test` or over a sibling someone else controls.

Redirects are **revalidated, not followed**. Authority belongs to a destination, not to whatever that
destination later points at, and this is the exact mechanism by which an approved staging URL becomes
a production one. Cloud metadata endpoints are refused outright, because "local and private addresses
are allowed" must not quietly include them.

## Decision 3 — archive intake refuses four specific escapes

Absolute paths, parent traversal, links of either kind, and special files. A link is refused rather
than skipped because a link written early can be followed by a later member to write anywhere the
process can reach.

Nothing is executed. No hooks, no submodule fetching, no install scripts. `_git` is a narrow
allowlist of read-only operations rather than a general runner, because a generic "run git with
whatever" helper is how a config-specified hook or pager eventually gets invoked.

## Decision 4 — the manifest digest must NOT be unique

Recorded because the constraint looked obviously right and was obviously wrong once tested.

`sealed_manifest.manifest_digest` was declared `UNIQUE`. The first test that sealed identical inputs
twice failed on a duplicate key — and the failure was correct. CONTRACTS section 4 states that
repeated execution creates a new runId and a fresh fixture instance "even when source/journey are
unchanged", so two runs legitimately share a set of input digests.

Worse, the constraint would have forbidden the comparison the product exists to perform. INV-04
requires that a baseline and its candidate differ **only** by an approved patch, and comparing their
input digests is exactly how that is demonstrated. A unique index there would have made the central
verification step impossible.

Migration `0006` drops it and adds a partial unique index on `run_id` instead: one run has one seal,
while two runs may share inputs.

## Decision 5 — unobservable build identity stays unobservable

`build_artifact.identity_observable` is False for a deployment that cannot prove which artifact it
serves. Such a run may still execute; it simply cannot make a fully verified provenance claim, and the
column is how that limitation survives into every summary rather than being filled in with a plausible
digest. `capability_summary` returns `None` — unknown — before anything is sealed, not an optimistic
True.

## A latent defect in module 04, found here

`timestamptz` comes back from PostgreSQL in the **session** timezone, not necessarily UTC. The
formatter in use was `isoformat().replace("+00:00", "Z")`, which produced
`2026-09-11T05:00:00-07:00` on a machine in Los Angeles — a string every parser in this codebase
correctly refuses.

Module 04's reviewer raised exactly this as an unexecuted hypothesis and rated it low suspicion. It
was real. It stayed hidden because no test reloaded a cancellation timestamp from the database and
then *compared* it: `cancel()` refused earlier for a different reason, before reaching the
comparison.

Now centralised in `to_rfc3339_utc`, which converts before formatting and refuses a naive datetime.
Two regression tests cover the full request → reload → acknowledge → reload → cancel cycle with each
step in its own transaction.

## Consequences

Every environment needs at least one explicitly allowed origin, and observer and reset credential
references must differ — one identity that can both reset state and attest to it is not an
independent observer.

Environment changes are append-only: superseding creates a new manifest and links the old one, so
evidence citing the old identity keeps meaning what it meant.

## Rejected alternatives

- **Storing credentials in the manifest.** A manifest travels into every export that cites it.
- **Refusing dirty checkouts outright.** Would make local E0 development impossible; refusing the
  *claim* achieves the actual goal.
- **Following redirects and checking afterwards.** By then the request has been made.
- **A tree digest over content only.** A rename would be invisible.
