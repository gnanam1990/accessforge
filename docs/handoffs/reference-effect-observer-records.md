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
`finalSample` is false, `count` is null and `assertionObservations` is empty. They do not close the
ordinary observer stream; its existing final completion measurement must still run. Original
source records use the same sequencer, producer identity, digest and retention machinery. No
new schema migration or live product/source change was needed to implement this service.

Validation: eight focused synthetic lifecycle checks and two real isolated API/PostgreSQL
control-plane checks pass; Ruff and strict mypy pass. The real control-plane test still uses a
synthetic collector and synthetic STOP receipt. The protected database collector has separate
real PostgreSQL fixture tests. These facts are not combined into a claim of actual reader proof
or an end-to-end independently authenticated physical run.

Remaining: launch/own this service in the independent process, require its committed readiness
before native actions, wait for confirmed STOP before closure, prove original artifact retention,
and connect the finalizer to both ordered lifecycle records and the resolved frozen policy.
The current finalizer does not consume the new coverage. Do not construct an expected execution
window by copying arbitrary measurement JSON or interpret an unclosed READY as coverage.
