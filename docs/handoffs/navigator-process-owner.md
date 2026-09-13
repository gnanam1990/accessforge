# Private navigator process ownership

The trusted native embedding can now call `runProvisionedNavigatorExecution(bootstrap, navigator)`
to compose the existing physical reader bootstrap, a single Python navigator process, independent
observer closure and the native finish path. The operator child module is
`accessforge_orchestrator.navigator.operator`; `--help` is inert. Its execution flag is explicitly
`--allow-billable-model-call`, in addition to the current human model-consent record checked by every
reserved turn. Neither flag nor a child receipt creates run approval or reader-startup permission.

## Host contract

Provide a canonical-parent absolute Python executable path (virtualenv interpreter symlinks are
supported), exact dispatch reference, consent ID, complete sealed model profile, monotonic lease
deadline, cancellation signal and independent observer-closure callback. The trusted bootstrap
reference must match and the process deadline cannot extend the native lease. The environment is
explicitly restricted to the database URL, provider credential/profile paths and optional CA file.
No parent environment, GitHub token, repository token or observer credential is copied into the
child. Instance metadata discovery is disabled for the child.

The native token and full reference are sent once through inherited FD 3, never command arguments,
temporary files or stdout. The child accepts only a bounded, closed JSON envelope on a pipe/socket,
with canonical UUIDs and the reviewed model profile. It owns one NativeNavigatorSession, serially
advances only known action/reader boundaries and never reconstructs or retries an admitted call.

FD 4 carries at most one 2 KiB status record without model text, private paths, tokens or exception
details. Child stdout and stderr are ignored by the native owner. Receipt size, exact reference,
STOP acknowledgement and a clean process exit must all agree. The parent then requires independent
observer closure and calls the existing bridge finish, which independently requires actual native
STOP and server evidence closure. A child claiming STOP cannot by itself release a lease. Returned
server acknowledgement is not equivalent to the complete run evaluator or human acceptance.

## Failure behavior

No second process can be launched on the same bridge. Expiry/cancellation fences the bridge and
terminates only the child this owner created; a still-running child receives SIGKILL after the
bounded grace period. Missing, malformed, foreign, oversized or nonzero-exit output cannot reach
finish. Late observer completion after cancellation cannot cause a delayed finish. Failure remains
unconfirmed; reconciliation inspects the original run and invocation, not a re-launch. Closing the
transport is not proof that the OS is quiescent.

## Evidence and remaining gates

Local TypeScript typecheck/build, scoped Python static checks and operator help are the validation
performed here. Existing CI exercises synthetic child processes with actual private pipes, receipt
faults, environment refusal and cancellation, plus pure operator protocol and serial ownership.
These are not real AT or paid-provider acceptance tests. No live model call, reader startup, TCC
change, runtime migration, deployment or OS reset was performed.

The production reader capability matrix remains empty and the runner's default binary remains
fail-closed. A packaged application still has to provision the trusted bootstrap callbacks and
operator UI, prove the actual reader profile, retain model identity evidence, and complete E0/R1
platform/human acceptance. This delivery provides the concrete launch/finish owner without
substituting a fake profile or weakening those requirements.
