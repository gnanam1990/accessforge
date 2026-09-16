# Independent collector process protocol (C2, partial)

The explicit POSIX worker is `python -I -m accessforge_orchestrator.reference_effect_worker
--private-host`, launched by trusted host infrastructure with inherited private pipes on FD 3
(host input) and FD 4 (receipts). This is not an interactive shell command or model tool.
It neither provisions credentials nor starts a reader. Normal invocation without that flag or
without pipes is refused. Parent environment isolation must be enforced by the launcher.

The private startup JSON line contains exactly `protocol`, `workspaceId`, `runId`, `attemptId`,
`observerCredentialRef`, `applicationRole`, `installationId`, and integer `maxWallSeconds` (1–1800).
The protocol is `accessforge.reference-effect-observer.v1`. Database URLs are read only from
`ACCESSFORGE_DATABASE_URL` and `ACCESSFORGE_OBSERVER_DATABASE_URL`, not command arguments or
model-owned input. The process requires its expected original attempt when reconstructing the
protected observer context; it cannot acknowledge a different current attempt for the same run.

After collector startup AND READY commit, FD 4 emits a compact `READY_RETAINED` receipt with
protocol/workspace/run/attempt/event ID. No fixture nonce, credentials, counts or interval data
leave the worker. The host later sends exactly one FINISH line with `protocol`, `command: FINISH`
and the original `readyEventId`, then closes FD 3. Duplicate keys, oversized inputs, extra records,
wrong receipt identity or EOF without FINISH are refused. FINISH is not STOP authority: the
service independently requires original settled successful STOP before closing the collector.

After CLOSED commit, the worker emits `CLOSED_RETAINED` in the same receipt shape. The host must
require both that exact receipt AND clean exit, not receipt arrival alone. It must also monitor
the process while the reader runs and fence later input on failure. Startup input has a five-second
bound; the process arms its requested wall deadline and handles SIGTERM/SIGINT/SIGALRM as failure.
Failure aborts the collector and reports a static reconciliation message, never a successful
closure, restart instruction or secret-bearing traceback. A parent must still own hard process
termination if an OS/database call does not respond to a signal.

Validation: 27 focused worker/service checks pass, including real inherited-pipe child processes
with wall-time expiry and SIGTERM. Those child tests use a synthetic observer and do not prove
database authentication, physical STOP or actual reader behavior. Source measurement and real
control-plane admission have their separate tests; they are not an end-to-end acceptance run.

## Native host integration

The private native operator configuration can now opt into `navigator.independentEffectObserver`
alongside its mandatory `independentObserver`. It supplies the independent Python executable,
credential reference, application role, installation ID and explicit observer-only environment.
The effect and completion observer credential references and product/source database URLs must
match; missing/contradictory configuration never selects a fallback source. No observer environment
is added to navigator input or inherited from the host process.

The bootstrap starts one worker after machine-session opening, waits for committed READY, and
retains separate fresh reader-startup authorization. Repeated startup-authority checks reuse that
same worker. Physical-action authorization checks liveness before and after its existing callback.
Worker/receipt-pipe failure aborts the composed execution and fences later navigator/native input.
After original STOP, the host requires matching CLOSED, complete pipe framing and clean worker
exit before starting the ordinary final completion observer. The process owner enforces startup,
closure and whole-run deadlines and escalates termination of its own child after two seconds.

Twenty focused native process/observer checks pass with real child processes and synthetic
receipts, including wrong identities, duplicate READY, early exit/closed pipe, oversized output,
missing CLOSED, reused event ID, nonzero exit, trailing output, expiry, cancellation and preservation
of navigator launch consent. TypeScript compilation passes. This is process wiring evidence, not
actual database/reader/model/end-to-end acceptance. The verified reader matrix remains unchanged.

Retained artifact verification and finalizer consumption are now connected; see
`reference-effect-observer-records.md` and the current `finalize_execution.py` /
`reference_effect_evidence.py` join. This supersedes the earlier process-only pending list.
Still pending: actual configured native/reader/model execution and its original artifact proof.
The option is not enabled automatically, and its presence is not a completed physical
forbidden-effect assertion or coverage of effects outside the protected reference database.
