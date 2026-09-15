# Independent collector lifecycle records (C2, partial)

`ReferenceEffectObserver` is an operator-service embedding in the orchestrator. It owns a real
`ReferenceEffectCollector` and admits READY/CLOSED records through the existing OBSERVER
`EFFECT_RECEIPT` stream. Construction opens no connection. There is no public upload endpoint,
model tool, permission grant, installation, automatic migration or retry.

`begin()` reconstructs the sealed observer/environment/fixture and original accepted attempt,
requires the frozen reference-effect policy, then starts the protected database collector. It
rechecks that context and absence of ALL action intents before committing READY. That commit,
not constructor return or a local measurement, is the readiness acknowledgement. A prior READY
for the attempt is refused rather than replaced. Unknown commit outcomes require reconciliation.

`finish()` requires the same original context and last settled successful STOP action. It closes
the collector, validates the exact run/attempt/resource/clock/start boundary, and rechecks the
control-plane context before admitting CLOSED with a reference to the original READY event.
Application credentials and fixture nonce are not included in either source record. Nanosecond
ticks and counts use exact decimal strings, avoiding JSON safe-integer truncation.

The new records deliberately do not pretend to be the current-row task-completion sample:
`finalSample` is false and `count` is null. READY has empty `assertionObservations`; CLOSED now
contains versioned forbidden-effect conditions authored by the independent observer using its
configured installation, reserved fixture and frozen reference policy. They do not close the
ordinary observer stream; its existing final completion measurement must still run. Original
source records use the same sequencer, producer identity, digest and retention machinery. No
new schema migration or live product/source change was needed to implement this service.

Validation: eight focused synthetic lifecycle checks and two real isolated API/PostgreSQL
control-plane checks pass; Ruff and strict mypy pass. The real control-plane test still uses a
synthetic collector and synthetic STOP receipt. The protected database collector has separate
real PostgreSQL fixture tests. These facts are not combined into a claim of actual reader proof
or an end-to-end independently authenticated physical run.

The private worker/native composition owns startup and STOP closure (see
`reference-effect-worker-process.md`). Evaluator 1.12.0 consumes versioned CLOSED conditions
only after original retained bytes and authenticated streams are verified. Its join requires
unique matching READY/CLOSED records, the same final observer binding and canonical ordering:
READY before every original action pair, successful final STOP before CLOSED, CLOSED before
the ordinary final sample. Wrong source, scope, clock, action identity, sequence or condition
family is refused. Missing/historical conditions remain absent/UNKNOWN; the evaluator does not
turn counters into observer-authored conditions. Historical evaluator records are unchanged.

Remaining: actual private-operator execution and original artifact retention/finalization proof,
plus real reader/model/environment qualification. Synthetic join tests and separate database
collector tests do not establish that acceptance path. Do not construct execution authority by
copying arbitrary measurement JSON or interpret an unclosed READY as coverage.
