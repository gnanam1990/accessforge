# Historical functional regression evidence

`functional_regression_evidence.for_run` reads the original protected candidate regression receipt
after the gateway and reader lease have closed. It does not call the live endpoint authorization
path, renew a lease, issue approval, run tests or infer success from a report uploaded by a user.

It requires a terminal PASSED record with all eight independent invalid-input rejection/no-write
checks for email, name, category and description. Missing/partial evidence returns unavailable.
Original run/build/seed and endpoint bindings, exact reader lease/epoch, release-before-finish and
four distinct cleaned-up process identities are checked. Preview-observed fixtures cannot become
reader-bound evidence. The payload contains original hashes/check names and process identities,
not fixture nonce, credential values, worker token or a daemon connection URL.

This is the persistence input for subsequent retained-artifact and frozen FUNCTIONAL_VALIDATION
integration. It does not yet change `_regression_attestation`, an assertion outcome or patch status.
Consumers still need original artifact retention and runtime-build matching; a candidate receipt
cannot establish baseline validation or a successful accessibility repair. A PASSED receipt is
not authority to restart an endpoint or resend a form.

The existing real-container candidate reader lifecycle exercises this historical read after
cleanup, compares original artifact/seed identity and checks tenant isolation. This coverage is
queued on CI; local Docker/object-store execution was not available. Source Ruff/mypy checks run
locally. Desktop observations in that lifecycle remain synthetic, not actual reader acceptance.

## Retained candidate execution artifact

Candidate-bound sessions now declare FUNCTIONAL_REGRESSION before execution. STOP can acknowledge
the reader and close its streams while this artifact is still absent: the controller must return
from the candidate gateway callback, allow protected regressions/cleanup to complete, then retain
the execution bundle. Waiting for the completed artifact inside that callback would deadlock the
producer. Unfinished/failed/partial regression receipts cannot be converted into retained success.

The artifact wraps the original historical receipt with the exact execution attempt, manifest and
producer identity, checking the original reader lease/epoch again. It has no invented canonical
producer stream. Bundle retention and finalization regenerate the original bytes from protected
records and compare them to object storage. Migration0055 admits the new artifact kind in the
database; it does not backfill historical declarations or snapshots. The upload content-type
allowlist restricts this kind to JSON. Outcome-bearing retention/deletion classification
matches the other execution evidence, not disposable diagnostic logs.

Existing candidate sessions without the original declaration cannot be silently upgraded. Reconcile
and drain them before deploying this requirement; do not manufacture an old declaration to resume.
This adds retention, not functional assertion evaluation or patch verification acceptance. The
real-container lifecycle additionally exercises snapshot lease mismatch, quarantine, promotion and
original byte readback. That coverage runs on CI; no local reader, deployment or live migration ran.
