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
