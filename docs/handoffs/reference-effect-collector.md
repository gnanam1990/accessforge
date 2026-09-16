# Reference committed-effect collector (C2, partial)

`ReferenceEffectCollector` in the persistence evidence package measures one protected reference
database installation and one reserved fixture. Its scope digest explicitly names
`PROTECTED_REFERENCE_COMMITTED_INSERTIONS_V1`, installation ID and fixture nonce. It does not
measure email, payment, external submission, unrelated databases or uncommitted SQL attempts.
The concrete resource digest is not the journey policy digest; a matching digest alone is not
authentication. Fresh fixture nonces are allocated per run and cannot be frozen into a reusable
journey assertion in advance.

`REFERENCE_EFFECT_POLICY_DIGEST` in the domain reference-scope module freezes the narrow resolution
rule: the controller-reserved fixture through the sealed environment's independent observer.
`ReferenceEffectBinding` separately carries the trusted run/attempt and actual installation/nonce.
`reference_effect_monitor_assertions` requires that binding to match the expected measurement
window, then evaluates only this specific frozen policy. Neither the frozen assertion nor the
concrete measured digest is rewritten. Different run, attempt, installation, nonce, effect or
policy stays UNKNOWN; the existing exact-concrete-scope evaluator is unchanged. Admission must
load this binding from protected controller/source configuration, never from measurement JSON.

## Actual measurement boundary

The opt-in audit installer now puts a shared transaction advisory lock in the protected ALWAYS
creation trigger. It lasts until commit/rollback, not just until INSERT returns. The collector
owns a separate installed observer connection and takes an exclusive session lock only at start
and end. Between boundaries it holds no lock, so normal application writes are not suppressed.
This uses PostgreSQL's [shared/session/transaction advisory-lock semantics](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS).

1. `begin()` drains earlier writers, verifies the protected installation in a fresh snapshot,
   and requires zero committed history for the exact fixture. Prior effects are refused, never
   subtracted or deleted. It samples the start clock before releasing the barrier. Dispatch must
   not begin until this call succeeds.
2. The protected history continuously records committed insertions, including requests later
   deleted. Application and observer roles cannot rewrite that history or disable the trigger.
3. After independently confirmed runner STOP, `finish()` drains in-flight writers with the end
   barrier. It samples the end clock, then reads a new protected history snapshot while still
   excluding later commits. That snapshot is established AFTER acquiring the barrier, not before
   waiting for writers. The resulting interval includes exactly this fixture's committed effects.
4. Closing the original connection releases the end barrier. Writes admitted afterward are
   outside the completed interval. Abort, disconnect, weakened protection, prior effects, clock
   reversal/expiry or failed closure produce no coverage. The collector cannot reconnect/replay.

The caller exclusively owns the connection and collector; do not share either with another
thread/operation or use a transaction-pooling proxy. Database administration is trusted, exactly
as for the protected audit source. The barrier applies database-wide to reference insertions,
so use a dedicated reference database. It does not add write privileges to the observer. SQL
lock acquisition has a five-second statement timeout; a stalled database/network is not proof
of closure and must not be treated as an empty interval.

## Provisioning and acceptance limits

No live installation or migration was performed. Older installations without the locking trigger
are now refused by exact trigger-definition checks. There is no automatic upgrade or reset:
provisioning a new empty isolated installation remains an explicit administrative operation.

Twenty-five focused checks passed on real disposable PostgreSQL databases, including positive
create/delete, rollback, complete absence, pending-commit drainage, late-write exclusion, prior
history refusal, source weakening, disconnect, abort, replay and invalid clock windows. These
are synthetic fixture effects on a real database, not actual VoiceOver or canonical run evidence.
Thirty-eight focused domain checks also pass, including fresh-nonce policy stability, separate
resource identities, substitution refusal and preservation of missing/positive coverage results.

The original collector-only checkpoint above predates the now-built start/STOP handshake,
authenticated original READY/CLOSED records and finalizer binding. See
`reference-effect-observer-records.md` and `reference-effect-worker-process.md` for that
composition and its backend evidence. Actual configured native/reader/model execution and
original physical evidence remain unaccepted. The measurement scope remains narrow; do not
manufacture an expected window from the returned measurement or claim arbitrary external-effect
coverage. C2 physical acceptance is not complete.
