# Captured source lineage in runtime evaluation

New server-owned artifact measurements retain the original candidate source-to-build lineage when
it exists. The trusted preparer captures the actual patched source tree and archive before dispatch;
the builder binds that archive to its durable process receipt and retained output. Publication follows
bounded output readback. The measuring coordinator rechecks the source snapshot, published artifact,
process receipt and causal timestamps, then attaches this lineage to the measured deployed artifact.
No public request supplies the lineage, and this read neither publishes nor backfills materializations.

The finalizer reads this data only inside already-verified original canonical preflight artifacts.
Every original action must have a matching fresh BUILD measurement and the same captured source tree.
Missing historical lineage, partial coverage and conflicting source trees leave SOURCE absent.
Malformed, foreign or causally impossible provenance is refused. The observed value is the captured
input tree, never the sealed expected source or the output artifact digest. A mismatched sealed source
continues through the existing identity mismatch gate; no identity comparison is bypassed.

This is **captured build-input lineage, not a runtime source filesystem read**, process-memory
attestation or a live reader demonstration. Receipts remain immutable; cleanup does not enrich old
records. Evaluator version is now 1.3.0, and previously stored evaluations replay unchanged. Other
unobserved identities still prevent verified completion; this change does not make an E0/R1 claim.

Validation is scoped Ruff/mypy and diff checks locally. Existing CI cases cover the real isolated
source/build/measurement chain and tampered source/artifact records; small synthetic interpreter
cases cover missing/conflicting lineage, wrong contexts and timestamps. No local full suite, provider
invocation, deployment, permission change or actual reader startup was performed.
