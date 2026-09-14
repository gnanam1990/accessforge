# Original baseline reader dispatch

The trusted `execute_baseline_session` callback can call
`accessforge_orchestrator.baseline_reader_dispatch.admit_and_dispatch_reader` with its
`BaselineSession`, the operator-selected runner/attempt IDs and a qualified `StartTransport`.
This joins the original bounded reader admission to `ManualRunController.dispatch`; callers
no longer assemble a run/lease/epoch reference from separate configuration.

The helper checks transport availability before reserving the sole original reader lease.
Admission still requires the matching original preflight, endpoint and approved sealed scope.
After admission commits, it reads the exact workspace/run/reader-lease/epoch/attempt binding
and current run revision, then passes that reference to the ordinary dispatch controller.
The controller freshly checks authority and commits its one-shot claim before sending a ticket.
The default transport refuses before admission; this does not enable production startup or
alter the empty verified VoiceOver matrix.

The return value is **delivery acknowledgement, not STOP, reader evidence or a run verdict**.
Keep the protected baseline callback alive until the native supervisor has completed its work,
closed the independent observer and recorded the exact acknowledged STOP. Returning immediately
after dispatch can leave a live reader and therefore makes cleanup unconfirmed. After the whole
protected runtime successfully returns, use the
[post-STOP completion command](stopped-execution-completion.md) to retain/evaluate evidence.

There is no automatic retry or substitute reader. If admission, binding read, or delivery fails,
inspect and recover the original attempt. An admission acknowledgement can be lost after commit.
An expired lease is not physical stop proof. The existing baseline cleanup gate retains UNKNOWN
when the original reader stop cannot be confirmed.

Ten synthetic composition checks cover ordered handoff, exact reference, default refusal,
invalid timeout and each exception boundary without redispatch. Existing persistence/manual
controller tests cover their own gates separately. A fully composed database/native transport
run and actual VoiceOver qualification remain pending; these unit checks do not prove either.
