# Admitted native navigator turns

`NativeNavigatorSession` composes the production retained-reader loader, explicit model consent,
shared budget ledger, PostgreSQL planning checkpoints, real Strands factory and authenticated
same-host native action transport. It is a callable coordinator, not a background retry job or a
provider/desktop bootstrap. Importing or constructing it does not invoke a model or start AT.

## Required caller boundary

The trusted host supplies the original private capability from
`startProvisionedNavigatorExecution`, the exact six-field dispatch reference, the human consent ID
and the independently configured `NavigatorModelProfile`. That native bootstrap still requires its
own explicit execution/bootstrap consent and actual platform capability checks. The coordinator
does not discover credentials, grant consent, weaken platform gates or reconstruct a saved session.

Keep one coordinator object for the lifetime of that capability. Await `run_next_turn()` serially.
The initial boundary is zero. A known non-STOP native action permits the next sequence only after
its original reader evidence is retained. A pending reader boundary may be polled before a claim;
a reserved model call is never retried, even with a new operation ID or process. A returned
`next_action_sequence=None` closes this coordinator. No caller should instantiate another object
to bypass that disposition.

## Ordering and accounting

1. Load and validate original reader-only projection and exact sealed model configuration.
2. Commit the consent-scoped, one-shot invocation and its conservative shared token hold.
3. Retain an invocation-bound lifecycle checkpoint, then reload the original reader boundary and
   recheck current consent, execution approval, attempt, lease and invocation identity.
4. Enter the real Strands factory with its one explicit tool. The tool retains its proposal, then
   rechecks the same original boundary before handing a fixture-value reference to the native port.
5. The native host independently enforces actual focus/origin, policy, fixture, lease, current
   cancellation and action limits. No configured URL is labelled an observed origin in Python.
6. Persist final ledger disposition before returning. Known completed SDK invocation is RECORDED;
   provider construction/error/timeout/cancellation or unknown native effect retains the full hold
   as UNCONFIRMED. Only failure before factory entry can be NOT_CALLED. Unknown final accounting
   prevents a continuation result and leaves the durable reservation to block replay.

These are conservative allowance holds, not measured provider usage or financial caps. A
MODEL_CALL_STARTED planning checkpoint is intent, not proof that a provider was contacted.
Migration 0049 binds new checkpoints to their exact open invocation, attempt and consented model
identity. Full configuration/projection digests remain available through the immutable turn and
consent relationship. Legacy unbound checkpoints are not relabelled as admitted calls.

## Not established by this delivery

No provider/AWS request, actual VoiceOver/NVDA action, OS setting change, runtime database migration,
deployment or packaged-host acceptance was performed. The coordinator is not yet launched by an
operator UI/CLI or a packaged cross-process lifecycle owner. A production host must still invoke it
under the approved bootstrap, drain the independent reader/observer evidence and explicitly call
the existing native finish path. `close()` fences future input; it cannot attest OS quiescence,
release the desktop lease or finalize a successful run. Model-identity finalizer evidence and full
E0/R1 platform/human acceptance remain separate unfinished requirements.

## Verification scope

Focused lifecycle regressions use labelled synthetic provider, database and native boundaries:
commit-before-construction ordering, unchanged native capability across turns, value-reference
privacy, pre-call revocation, checkpoint failure, provider construction/error, lost native reply,
task cancellation, empty model output and unknown final accounting. Existing real PostgreSQL/HTTP
integration additionally covers reservation rechecks and durable checkpoint profile/operation
binding, uniqueness, immutability and refusal after terminal accounting. These tests run in existing
GitHub CI; no local full test suite was run for this delivery.
