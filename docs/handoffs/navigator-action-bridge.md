# Private navigator-to-desktop action transport

The provisioned execution bootstrap now has a native planner-port continuation:
`startProvisionedNavigatorExecution` loads the existing private startup-consent reference, acquires
the existing desktop claim, opens the one-shot machine session and initializes through the same
physical/profile gates. Only then does it expose an owned private Unix socket capability to the
trusted controller. The verified reader matrix and default unavailable desktop entrypoint are unchanged.

`NativeNavigatorTransport` is the Python implementation of the navigation gateway's dispatch port.
The gateway preserves the approved text-value reference alongside its existing resolved value;
the native transport sends the reference only. Raw typing, HTTP, source, observer data and arbitrary
commands have no wire fields. Native action admission still resolves the reference under its own
frozen policy and repeats current authority/origin/budget checks before an OS action.

Each request binds the full workspace/run/attempt/runner/lease/epoch reference, a private capability,
a request identifier and one strictly advancing ordinal. The bridge consumes the ordinal before
awaiting dispatch and never admits concurrent actions. The Python client cannot resume from another
instance or retry after transport ambiguity. Both sides fence on uncertain delivery. Known FAILED
results remain distinct from AMBIGUOUS; clean STOP stops further input but does not release the
desktop claim. Only trusted explicit finish after independent observer closure reaches the existing
journal/server-ACK release path. Closing a port is not proof that an already-entered OS call stopped.

Replies include the actual server action ID rather than the local lease:epoch:sequence journal ID.
No exception content, credentials, announcement or completion verdict is returned. Socket paths,
capabilities and independently provisioned dispatch references must never enter model context or
browser responses. Socket ownership/mode/path checks assume cooperative same-user host services;
they are not a sandbox against arbitrary malicious code running as that OS user.

Local validation: changed Python Ruff/mypy, desktop typecheck/build, diff checks. Focused native
socket and Python client cases are authored for existing CI, with synthetic runner responses; no
local full test suite, reader startup, OS changes, model call, deployment or actual AT proof occurred.

This closes the action-transport connection, not the entire navigator execution workflow. Still
needed: the production turn coordinator that loads original retained reader projections, durably
claims each billable model invocation with explicit consent/current authority, and binds the actual
model configuration into evaluation evidence. No external provider is invoked by constructing the
native transport, and this implementation does not claim that the full model/reader loop ran.
