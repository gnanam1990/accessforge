# Private live artifact probe

The trusted build controller can open `session.artifact_probe(private_directory=...)` inside its
existing candidate-session callback. It creates an owned mode-0700 temporary directory and a
mode-0600 Unix socket under an existing private host directory. Its `reference()` is a sensitive,
same-user local capability: hand it only to the trusted desktop bootstrap, never an API response,
browser, navigator, log, or evidence artifact. Closing the context removes its own socket/directory;
unconfirmed measurement-thread termination raises cleanup failure rather than claiming success.

Pass that reference and the independently sealed build digest as `physicalPreflight.artifactProbe`.
The desktop client performs a fresh request for each probe, validates private filesystem ownership,
exact deployment identity, request correlation and measurement time, then supplies the measured
digest to the existing build check. It overrides historical setup digests only when configured.
Failure, cancellation, timeout, malformed/oversized response or identity drift fences this client;
there is no cached digest fallback or automatic retry. A measured/sealed digest mismatch stays a
FALSE build check, not a rewritten expectation.

The server invokes the existing gateway measurement, including the coordinator's immutable receipt
retention callback and current candidate/lease authority. The runtime-preflight endpoint can then
attach the corresponding server-owned receipt. This bridge adds no browser route and cannot type,
start a reader, forward arbitrary URLs, grant RUN_EFFECTS, or execute caller commands. Unix sockets
require a same-host POSIX controller; remote workers and Windows need a separate authenticated host
transport. The production reader-profile and startup-consent gates remain unchanged.

Local validation is scoped Ruff/mypy and desktop typecheck/build, not a full test suite. CI fixtures
exercise actual local socket framing against synthetic measurements in Python and Node separately;
they do not prove an actual candidate-to-reader deployment or an accessibility repair.
