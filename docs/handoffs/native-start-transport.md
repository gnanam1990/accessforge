# Private native dispatch handoff

Scope: build-flow audit F1, original modules 08/10/12. This connects an admitted Python
dispatch ticket to the existing native execution bootstrap; it does not close G2.

The trusted operator calls `startNativeDispatchListener` with provisioned bootstrap and
navigator options, retains its completion promise, and supplies its privateReference to
`NativeStartTransport(private_reference, expected_reference)`. The latter implements the
existing StartTransport port used by the manual baseline dispatcher. Host callbacks and
credentials are never supplied by the socket client. The shipped diagnostic entrypoint is
still disabled: concrete operator runtime/action evidence configuration remains unfinished.

The host refuses unqualified reader profiles before creating a listener. It uses an owned,
private Unix directory/socket, a random one-run token, exact attempt/lease identity and a
bounded deadline. The Python client checks canonical ownership/mode and socket identity
before and after delivery. These controls assume trusted same-user host code, not a sandbox
against another process with the same user privileges.

HANDOFF_ACCEPTED proves delivery only. Existing native bootstrap independently redeems the
ticket and enforces startup authority. Startup failure can follow delivery; the operator
must observe completion and reconcile the original server attempt. Neither loss of a
receipt nor cancellation permits another send. Closing the listener requests cancellation;
it is not native STOP evidence or permission to delete claims. Private reconciliation
directories are retained; no recursive production cleanup is introduced.

## Validation and limitations

- Six Python tests use real Unix sockets with a synthetic host: exact receipt, foreign
  identity, duplicate fields, extra data, oversized response and cancellation/no reuse.
- One Node test proves the currently unqualified profile cannot advertise a socket.
  This is not a positive production-listener test or actual-reader acceptance.
- Direct installed TypeScript compiler, focused Python mypy and Ruff pass.
- No physical reader, provider call, live database migration or deployment was performed.

Next: finish the operator-owned entrypoint/configuration connection, then independent
forbidden-effect coverage (F3) and actual G2 evidence. Do not mark these done based on
transport or subprocess unit tests.
