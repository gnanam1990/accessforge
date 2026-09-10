# ADR 0007 — Physical desktop exclusivity, stop proof, and one mechanism removed

- **Status:** Accepted
- **Date:** 2026-09-10
- **Module:** 07 (desktop enrollment, leases and dispatch control)
- **Builds on:** [ADR 0005](0005-durability-and-the-outbox.md), [ADR 0006](0006-immutable-identity-and-origin-policy.md)

## Context

A runner is a supervisor process living in a signed-in interactive desktop with a real screen reader,
a real browser and real focus. Two attempts on one such desktop do not merely interfere — they type
over each other's focus, and every observation either of them makes is worthless. CONTRACTS section 5
states the requirement as "One active lease per physical interactive desktop session", and INV-10
restates it as an invariant.

Everything in this module is downstream of taking the word *physical* literally.

## Decision 1 — identity is the operating system's interactive session, never a process

`PhysicalSession` is `(device_id, platform, interactive_session_id, console)`, digested into one
opaque key. On macOS the session identifier is the console session's audit id; on Windows it is the
Terminal Services session id. `device_id` scopes it to a machine, because Windows session id `1`
exists on every host in the world. `console` is part of the key because a remote session shares a
machine and not a screen.

What is deliberately excluded is anything a supervisor chooses for itself: a pid, a container id, a
task ARN, a hostname, a boot id. Restarting the supervisor, or running a second copy of it, changes
every one of those while the screen stays the same — so keying exclusivity on any of them would
defeat the invariant it exists to enforce. `assert_not_process_identity` refuses those keys by name,
because the mistake it guards against is not a type error: plumbing a container id into
`interactive_session_id` type-checks perfectly and admits two attempts to one screen.

## Decision 2 — exclusivity is one mechanism, and it is the database

A partial unique index on `desktop_lease (workspace_id, session_key) WHERE released_at IS NULL`.
That is all. An integration test proves it by inserting a competing lease with raw SQL, bypassing
every check in the application.

This began as two layers: the index, plus a `pg_advisory_xact_lock` on the session key, described in
the module docstring as "the index is the guarantee and the lock is the ergonomics". Mutation testing
showed both halves of that sentence were wrong. Removing the index failed exactly one test, which is
the correct and useful outcome. Removing the lock failed **nothing at all** — `admit_lease` already
takes `SELECT ... FOR UPDATE` on the runner row, and that row lock serializes every contender for the
same desktop by itself. The claimed ergonomic benefit was not real either: the unique-violation
branch converts a losing race into the same `SessionBusy` the lock's own check would have raised.

The one case a per-runner row lock does not cover is two runner rows sharing one desktop, which is
reachable only by revoking a runner mid-lease and re-enrolling the same screen. That window is closed
at its source — `revoke_runner` refuses while a lease is active — rather than papered over with a
lock no test could distinguish from the index.

Module 04 recorded exactly this confound: an advisory lock and a unique index that no test could tell
apart. Keeping a second mechanism here would have repeated it with the lesson already written down.

## Decision 3 — a runner cannot declare itself ready

`PreflightResult` has no `ready` field, and a unit test asserts structurally that it never gains one.
A runner submits tri-state results for a closed vocabulary of fourteen checks, and the *server*
concludes readiness as a conjunction over all of them. Three consequences follow, and each is a
separate test:

- a FALSE check fails readiness;
- an UNKNOWN check fails readiness, because not knowing whether the screen was locked is not the
  same as knowing it was unlocked (INV-02's shape applied to readiness);
- an **omitted** check fails readiness, so a supervisor that silently stops reporting
  `NO_STALE_INPUT_SOURCE` after an upgrade fails loudly instead of quietly losing the guarantee.

Profile drift is checked by the server against the enrolled profile rather than trusted to the
runner's own `READER_VERSION_MATCHES_PROFILE` check. A supervisor willing to misreport a reader
version would also misreport whether that version matches.

## Decision 4 — two clocks, never interchanged

Lease deadlines are `performance.now()` / `time.monotonic()` readings. Audit timestamps are wall-clock
UTC. This is a correctness property, not a style preference: a wall clock moves under NTP correction,
daylight saving, an operator setting the date, or a VM resuming from a snapshot, and a deadline
compared against it moves too — backwards and the lease looks fresh for decades, forwards and it
expires mid-keystroke. A monotonic reading is immune to all of that and meaningless in an incident
timeline, which is why both exist and neither substitutes for the other.

The supervisor treats a monotonic clock that moves *backwards* as a fenced desktop rather than a late
lease. A monotonic clock going backwards is a broken premise: there is no longer any basis for
deciding when the lease ends.

## Decision 5 — expiry fences, it never reclaims

A lease past its deadline is released with reason `EXPIRED_WITHOUT_STOP_PROOF` and its desktop is
**quarantined**. The desktop is not handed to the next run.

A heartbeat that stops arriving means the network is down, or the supervisor is wedged, or the machine
is asleep. None of those is proof that nothing is typing — the supervisor has its own monotonic
deadline and may be most of the way through a keystroke. CONTRACTS is explicit: "Lease timeout alone
is not a verified stop; no replacement until stop/reset proof."

Quarantine leads only to `PREFLIGHT_REQUIRED`, never directly to `READY` and never to `BUSY`. A
successful reset proves the old session is dead; it does not prove the new one works.

## Decision 6 — terminal CANCELLED needs stop proof, and there are exactly two routes

Cancellation is metadata, not a status: `cancel_requested_at` and `cancellation_revision` are recorded
and the run stays non-terminal. What changes immediately is that no further action is admitted
(INV-13) and no new lease is granted.

`terminalize_cancellation` admits terminal CANCELLED only when:

1. the run was never leased at all — provable from the absence of **any** lease row, so no action can
   ever have been admitted to a desktop; or
2. a stop was acknowledged for the lease's *current* epoch with no action left unresolved.

Nothing else, and in particular not an expired lease. An acknowledgement while a `TYPE_TEXT` is in
flight says the supervisor stopped *asking*, not that the keystroke did not land. A silent supervisor
ends INTERRUPTED with an `ambiguityReason`, which is INCONCLUSIVE, because infrastructure ambiguity is
not a reproduced accessibility defect (INV-02).

## Decision 7 — intent is durable before dispatch, and nothing is ever replayed

The journal entry is written and `fsync`ed before the action reaches the operating system, so a crash
in between leaves a record saying "we asked for this and do not know what happened" — which is the
truth, and which makes INV-09's refusal to retry an informed decision rather than a guess. Without
the record the same crash is indistinguishable from nothing having happened.

`inspectJournalAfterRestart` returns a *report*, not a recovery. There is no recovery to perform: an
intent with no result is permanently unknown. A function that "recovered" would be a place for
someone to add a retry later.

Nothing is re-dispatched, consequential or not. `TYPE_TEXT` and `ACTIVATE` are listed as
consequential for *reporting*, because a second one is a second real effect — but a repeated `NEXT` is
also refused, since nobody could then attribute the reader's announcement to one press or two.

## Decision 8 — unavailability is always explained, never worked around

`match_runners` raises `RunnerUnavailable` with a machine-readable reason rather than returning an
empty list. `NO_RUNNER_FOR_READER`, `NO_RUNNER_FOR_READER_VERSION`, `ALL_MATCHING_RUNNERS_QUARANTINED`
and `NO_READY_RUNNER` lead an operator to four completely different actions.

An empty list would invite a caller to fall back to something that is not a screen reader, producing a
confident result from a browser no screen-reader user ever used. That is the single outcome this
product exists to refuse.

## Decision 9 — a gap closed between modules 04 and 07

`lease_epoch` existed on `RunState` and on the `run` row from module 04, was written by every
transition, and was never *set* by anything, because no reducer took an epoch. The failure mode was
silent: a run's epoch stayed `0` for life, so `acknowledge_stop` — which refuses an acknowledgement
whose epoch does not match the current one — would have refused every acknowledgement that could ever
exist. A stop could never be proved and no run holding a desktop could ever reach terminal CANCELLED.

`admit_to_desktop` is the missing reducer, and `admit_lease` applies it in the same transaction that
grants the lease. Anywhere else leaves a window in which a desktop is held by a run that does not
know which session holds it.

## Consequences

- Exclusivity does not depend on application sequencing, and a test proves that by bypassing it.
- Every refusal in this module is a named reason an operator can act on, not a boolean.
- A desktop that might still be acting is never reused, which means operator intervention is a normal
  part of running this system rather than an exception. That is the intended trade.
- **Nothing here proves operating-system containment.** A lease is a record in a database. Whether it
  actually prevents another process from sending input to that screen is module 08's and module 09's
  physical-boundary tests to establish, and is UNVERIFIED until they run.
