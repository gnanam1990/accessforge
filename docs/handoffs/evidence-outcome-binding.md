# Offline evidence outcome binding

The offline verifier now compares the readable `machineOutcome.outcome` and
`evaluatorVersion` with the corresponding signed manifest fields. Previously,
changing only the readable FAIL to PASS (or changing the named evaluator) left
the manifest signature valid and the verifier's integrity report clean.

The check rejects unknown/non-string outcomes, invalid evaluator versions,
non-object machine outcomes and malformed reason lists. A disagreement yields
`outcome.frozen: FAILED` and verifier exit code 1, even if the original manifest
signature still verifies. Existing correctly matching bundles need no migration
or re-export. Signature attribution remains separate from content integrity.

Eleven focused synthetic archive checks cover the genuine Ed25519 signature
path, a clean control, outcome/evaluator alteration, malformed fields and the
remaining unsigned explanation boundary. No reader, browser, model, database,
object-store service or OS permission change is used by these checks.

Scope limitation: this format signs the outcome and evaluator, not the readable
reason text. The finding now explicitly labels that text as outside the
signature. This change does not establish that physical events occurred,
recompute a verdict, attest human usability or implement retained benchmark
ingestion. Full benchmark integration and actual acceptance remain outstanding;
actual VoiceOver runtime testing stays paused at the owner's request.
