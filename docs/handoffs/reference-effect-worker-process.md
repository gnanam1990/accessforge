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

Still pending: native launcher integration before physical actions, independent child liveness
fencing, closure before the ordinary final observer sample, original artifact verification, and
finalizer consumption. No real worker/reader was launched against a live application.
