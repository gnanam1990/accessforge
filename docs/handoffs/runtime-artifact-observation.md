# Live deployed candidate artifact measurement

The owned reference runtime now reads back its deployed artifact before application
startup and before/after each browser gateway request. The trusted pinned image's
tar binary exports the bounded artifact directory through the existing sandbox
transport. Host parsing is inert: no extraction or candidate-code import. Canonical
readback verifies the entire file set, bytes and regular-file modes against the
retained immutable artifact. Symlinks, unsafe archives and changed bytes/modes refuse.

The gateway's trusted-controller-only `observe_artifact()` capability performs a
fresh read, with current endpoint/lease authority and Docker daemon/process/config
checks. It returns the measured artifact archive/tree digests, task/container/image/
daemon binding and observation time. It is not exposed as a browser route. Missing
configuration does not synthesize a measurement from a launch receipt. Wrong/stale
or extra-field results fence the gateway.

Gateway request samples share the existing five-second transport deadline, without
extending the endpoint or desktop lease. If post-request measurement fails, the
response is unavailable and the request is not replayed: a POST may already have
had its effect. Existing exact-container cleanup and UNKNOWN handling still apply.
The runtime policy digest includes the new executing measurement and gateway code.

## What this does and does not establish

This establishes deployed filesystem bytes at measurement time. It does not attest
process memory or defeat a malicious change-and-restore between samples. The
artifact tree digest is not a source-tree digest. SOURCE/ENVIRONMENT/MODEL identities
are not copied from a seal or invented from these build files. The controller still
needs to integrate the measured identity with canonical runtime evidence; the
finalizer and verified-reader matrix remain unchanged. No actual-reader repair or
production deployment is claimed.

Scoped Ruff/mypy and diff checks run locally. Existing isolated-build CI coverage
now reads the live artifact and rejects a deliberately changed deployed file mode;
small contract cases cover stale/foreign measurement responses. These fixtures are
for CI only. No local Docker regression suite, real reader, paid model call, host
settings change or deployment was executed for this implementation.
