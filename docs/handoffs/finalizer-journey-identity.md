# Original journey identity at finalization

Newly compiled journeys retain the exact existing journey hash preimage in the protected
reviewer summary. Compilation digests and navigator-visible policy remain unchanged. There is
no migration or backfill of immutable historical versions.

The finalizer rehashes that original contract, checks its assertion, fixture and policy bindings,
and rehashes the stored navigator policy. Only then does it report `JOURNEY_VERSION` as observed.
This establishes logical contract identity, not physical execution, fixture-instance identity,
environment, runner or model attestation. Missing historical preimages remain unobserved;
present but corrupted or misbound contracts refuse finalization.

Evaluator version is now `1.4.0`. Existing immutable evaluation snapshots replay unchanged;
new finalizations sealed to another evaluator version remain inconclusive on that mismatch.
The original assertion contract continues through its existing independent validation path.

Changed-file static checks are the local gate. Focused preimage, legacy and mismatch regression
cases are included for CI; no actual screen reader, provider invocation or deployment is involved.
