# Protected functional producer outcomes

The owned reference driver now returns a typed `ValidationObservation`: the exact
suite digest and each invalid field's HTTP status and independently queried row
count. Candidate stdout and human-readable check names do not supply these values.
Partial, reordered, duplicate, malformed or different-suite observations refuse.

The fenced regression finish transaction accepts a complete passing observation,
loads the original candidate run's frozen assertion contract, and authors only
`FUNCTIONAL_VALIDATION` conditions. A matching protected rule receives `TRUE`;
prose-only functional assertions receive `UNKNOWN`. It binds those conditions to
the original workspace, run, manifest, assertion set and released reader lease.
Runs without a reader lease or historical executable contract retain measurements
without acquiring run-bound assertions. The existing regression failure path
continues to fail/mark unknown; this change does not publish partial failure cases.

Migration 0056 stores the producer receipt with the successful terminal transition.
Terminal records cannot gain, replace or remove that receipt. Old rows stay NULL;
an old worker's receipt is not reconstructed from check names. Retained functional
artifacts include the original receipt when present, preserving the old byte shape
when absent. The artifact remains mandatory for candidate sessions, but does not
invent a canonical event stream; bundling and finalization share that distinction.

Evaluator 1.10.0 consumes the original conditions only after retained artifact
verification, exact run/attempt/manifest/assertion-set/lease binding, and agreement
with independently observed runtime build identity. It preserves observer-authored
provenance and references the retained artifact digest; it does not derive backend
conditions from check names. Missing runtime build or historical producer evidence
leaves assertions UNKNOWN. Conflicting bindings, duplicate conditions and attempts
to supply another observer's assertion family refuse. Existing evaluations remain
immutable, and older sealed evaluator identities are not silently upgraded.

This functional condition alone does not establish a passing run. The patch
verification gate remains closed pending full proof integration. Actual
VoiceOver/NVDA, billable model calls, deployment and live migration acceptance are
not claimed.

Validation covers synthetic measured conditions and malformed inputs, a fresh
disposable PostgreSQL forward migration, and the existing real-container/S3
candidate lifecycle extended to verify authored conditions and terminal receipt
immutability. The container lifecycle is CI coverage, not a local AT run.
