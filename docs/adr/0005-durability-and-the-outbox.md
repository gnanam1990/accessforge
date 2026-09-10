# ADR 0005 — Durability, the outbox, and what an expired claim proves

- **Status:** Accepted
- **Date:** 2026-09-10
- **Module:** 04 (authoritative journal and transactional outbox)
- **Builds on:** [ADR 0004](0004-tenant-isolation-and-the-role-matrix.md)

## Context

Module 04 had to make accepted work survive process loss without duplicating desktop effects or
inventing completed work. Every decision below follows from one question: after a process dies at an
arbitrary instant, what can actually be proved about what happened?

## Decision 1 — a transactional outbox, and at-least-once delivery stated openly

State changes and their queue messages commit in one transaction. Publication happens afterwards,
which makes delivery at-least-once — never exactly-once, because no queue provides that.

The alternative, publishing first, inverts the failure mode into the dangerous direction: a message
that survives a lost commit lets a consumer act on work that was never accepted. A duplicate
delivery of real work is recoverable; a delivery of imaginary work is not.

Three consequences are designed in rather than worked around:

* Messages carry a **reference**, not a command. The consumer re-reads authoritative state, so a
  stale or duplicated message cannot justify an action the current state does not.
* Every message carries a stable ``operation_id``, so consumers deduplicate on business identity
  rather than delivery identity.
* Redelivery is **visible**: the attempt counter increments. A silent retry hides the fact that
  something went wrong the first time.

## Decision 2 — an expired claim proves nothing about the work

This is the distinction the module turns on, and it decides what may be retried.

A **job** whose claim lapses is re-claimable. A job is a database operation, and re-reading state
makes a repeat harmless.

A **desktop action** whose result was never recorded may never be retried. The intent was made
durable before dispatch, so a keystroke may already have reached the operating system, and a
visibility timeout expiring is not evidence to the contrary (INV-09). Those attempts are surfaced by
`recovery.ambiguous_attempts` for quarantine. Nothing reschedules them.

`recovery.py` is where this lives, and its docstring says so, because the tempting refactor — "treat
an ambiguous attempt like any other stale claim" — is exactly the bug.

## Decision 3 — the reducers remain the only transition authority

`runs.apply_transition` loads a row, hands it to a module 02 reducer, and writes back the result. It
never decides a transition. Reimplementing the rules in SQL would create a second definition of the
state machine, and the two would eventually disagree about something that matters.

The database still enforces the **outcomes** of those rules independently: a CHECK constraint for
admissible status/outcome pairs, a CHECK that an interrupted run records why, and a trigger refusing
any mutation of a terminal run. Those cover every path the reducers never see — a new repository
method, a report, a support query, a migration.

## Decision 4 — four identities in the evidence chain, kept distinct

Canonical sequence, producer sequence, source record id, and source record digest are separate
things, and conflating any two breaks something specific:

* Canonical sequence is the **sequencer's** ordering. Producers submit records; they do not choose
  their position. Two producers both submitting their own sequence 1 land at canonical 1 and 2.
* Producer sequence detects that producer's own gaps. Records are staged until contiguous, because
  admitting out of order would let the chain look complete while a producer's middle is missing.
* Source record id keys replay detection; the digest distinguishes a replay from a conflict. Same id
  with the same content is idempotent; same id with different content is **rejected**, because one of
  the two submissions is wrong and guessing which would corrupt the chain.

Serialization is a transaction-scoped advisory lock keyed on the attempt, so distinct attempts never
contend.

## Decision 5 — closing watermarks, because a contiguous chain is not completeness

A producer can go silent mid-run and leave a canonical chain with no gaps at all. Completeness
therefore requires an authenticated closing watermark per required producer, and the watermark must
match what was actually admitted — a producer claiming to finish beyond its admitted tail is claiming
records the sequencer never saw (INV-06).

A required producer with **no stream at all** counts as not closed. Absence is not completion: a
producer that never spoke is indistinguishable from one whose records were lost.

## Decision 6 — the AWS transport raises rather than pretends

`LocalTransport` is explicitly configured for tests. `AwsSqsTransport` exists to keep the interface
honest and raises `NotImplementedError`. Provisioning a queue is billable and needs authority this
module does not have, and a configuration flag that silently degrades would eventually become a claim
that AWS delivery was tested.

## Two guards that were present but unprovable

Worth recording, because mutation testing found the **tests** wrong rather than the code:

**The optimistic revision check was unreachable.** `apply_transition` re-read state under a row lock
and then wrote back the revision it had just read, so the comparison could never fail. Removing the
predicate failed no test. A guard that cannot fail is indistinguishable from an absent one, so the
signature now takes the revision the *caller* last observed — which is the real concurrency question
— and a stale caller is refused.

The statement-level predicate is kept as belt and braces for a future caller that reaches the UPDATE
without the lock. It has **no test**, deliberately: it is unreachable through the public API while the
row lock is held, and an earlier attempt to cover it by asserting on the function's source text was
removed during review. Such an assertion proves a string exists, not that the executed statement uses
it — it would pass with the predicate moved into a comment and fail on a harmless reformat. An
untestable guard recorded as untestable is more honest than a test that cannot distinguish presence
from correctness.

**The advisory lock was indistinguishable from the unique index.** A race test expecting a lock
timeout passed with the lock removed, because the second connection blocked on the
`canonical_event` primary key instead. The test now observes the lock directly in `pg_locks`. The
constraint would still catch a collision, but as a crash rather than as serialization, and that
difference matters to whoever reads the incident log.

## Consequences

Callers that transition a run from outside a single transaction must pass `expected_revision`. That
is slightly more work at each call site and is the point: the revision is the caller's evidence that
its decision is still valid.

`fixtures/reference-app` keeps its own simple schema and does not use any of this. It is the
application under test, not part of the product.

## Rejected alternatives

- **Publish then commit.** Inverts the failure mode into the dangerous direction.
- **Exactly-once delivery.** Not available; pretending otherwise moves the duplicate handling
  somewhere less visible.
- **Retrying ambiguous desktop actions after a timeout.** The specific thing INV-09 forbids.
- **Encoding the state machine in SQL.** Two definitions of the same rules, diverging silently.
