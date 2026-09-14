# Baseline operator completion

`accessforge_orchestrator.baseline_completion.dispatch_and_complete` is the synchronous
trusted-host entrypoint joining protected baseline runtime to original reader dispatch,
positive STOP wait and post-closure retention/finalization. It delegates to existing
admission, one-shot dispatch and evidence validation boundaries without bypassing them.

Supply exact workspace/run/build and reader attempt/runner identities, authorized origin,
reset/observer credential references, protected regression runner, archive/evidence stores,
original private journal path, and qualified `StartTransport`. These are host capabilities,
not model tool inputs. No transport is installed or qualified by this function.

Before runtime provisioning it rejects unavailable transport, cancellation, invalid timeout
and invocation inside an active event loop. Reader wait is bounded to 60 seconds; this is
not a deadline for the entire build/retention workflow. The native supervisor still owns
physical cancellation, independent observer closure, fencing and actual STOP. Cancellation
checks before admission are not a replacement for that supervisor.

Ordering: protected runtime → admit/dispatch once → original acknowledged STOP → protected
runtime closure/receipt → original journal retention → deterministic finalization.
Uncertain STOP or runtime closure propagates without finalizing or redispatching.
Reconcile the original attempt; do not replay this entrypoint. After independently confirmed
closure, evidence-only recovery uses the existing command in
[post-STOP completion](stopped-execution-completion.md).

Focused tests are synthetic composition checks, not real reader/model/container/S3 acceptance.
Actual VoiceOver qualification and concrete native transport provisioning remain necessary.
