# Original evaluated regression gate

Patch conclusion reads protected regression evidence only for the candidate run
bound to that exact verification and its original baseline. Missing or unrelated
bindings leave the gate unmet; callers still cannot supply an attestation boolean.

Both original evaluation snapshots must exist and match their currently completed
runs and seals. The baseline must be FAIL; the candidate must be PASS or FAIL, not
INCONCLUSIVE. Each snapshot's required observed identities must match its sealed
identities. The existing patch-comparison gates separately require candidate PASS
and only the approved/explicitly permitted identity differences before VERIFIED.

The historical regression receipt must have complete validation, authorization,
submission, retry, restart and cleanup check coverage. Its originally authored
functional conditions must appear as TRUE in the candidate evaluation, with
OBSERVER_AUTHORED provenance and the exact retained functional artifact digest.
Its artifact identity must agree with the observed runtime build, not just the
expected seal. Historical check-only receipts do not gain functional conditions.

The functional artifact is reconstructed from the original immutable producer
receipt and matched to the evaluation's artifact list. Both evaluations' artifact
rows must remain PROMOTED/RETAINED and match the original run, attempt, producer,
kind, digest and manifest; share locks preserve these metadata facts during the
conclusion transaction. This gate relies on the finalizer's original object-byte
verification; it does not perform a new object-store download during conclusion.
Corruption refusals are not converted into a successful gate.

Focused coverage uses synthetic original-row policy fixtures and a disposable
PostgreSQL negative path showing that complete-looking manual run outcomes cannot
produce VERIFIED. The real-container lifecycle also checks that successful worker
regressions without original evaluated reader pairs remain unattested. This is
not an actual VoiceOver/NVDA matched-pair acceptance result. Baseline functional
producer coverage, physical reader qualification and a complete authorized
baseline/repair/rerun/review demonstration remain required for end-to-end delivery.
