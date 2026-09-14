# Navigator runtime evidence

The production navigator now observes actual botocore request parameters and response metadata,
plus completed Strands streams. It compares the constructed model, retry strategy and enforced
invocation limits with the reviewed profile before invocation. Every request (including the SDK's
tool-result compatibility fallback) is counted against the same maximum attempts, with transport,
region, model and inference parameters checked again before provider entry. Cancellation fences
detached SDK workers from issuing further requests.

Receipts contain only the complete configuration, bounded request IDs, HTTP statuses and stream
completion flags. No prompts, reader announcements, response text, credentials or tool arguments
are retained here. A constructor, reservation or `MODEL_CALL_STARTED` checkpoint is not proof.
Missing response metadata, a partial stream, active worker or cancelled call produces no receipt.

Migration 0050 adds an immutable, tenant-isolated runtime observation bound to its original open
admitted invocation and consent profile. Admission atomically declares a `MODEL_RUNTIME` artifact.
Post-STOP retention builds this artifact from original settled ledger/receipt/action bindings;
finalization verifies its bytes against those original sources. Unsettled calls block retention;
unconfirmed or missing observations never establish MODEL identity. Every action must have its own
recorded model turn and matching native action ID; a favorable subset is insufficient.

The observed MODEL identity means **client-observed requested configuration and completed response**,
not provider-signed attestation of internal model weights, execution region behind a global inference
profile, financial usage or physical AT. These limitations remain explicit in the receipt itself.
Evaluator version is 1.5.0; old immutable evaluations replay unchanged, and old evaluator seals still
require an exact version match. Historical missing evidence is not backfilled or silently upgraded.

Verification uses changed-file Ruff/strict mypy and narrowly scoped synthetic SDK regression cases.
Six local SDK cases passed: completed/partial/over-budget/wrong-model/cancelled worker observations,
and a full real Strands/botocore invocation with an in-process stubbed response. No network provider
call was made; these checks establish wrapper behavior, not external provider acceptance.
CI additionally covers real PostgreSQL profile binding, immutability, cross-workspace isolation,
settled snapshot requirements, forward migration, and finalizer missing/corrupt/partial coverage.
No billable call, actual screen-reader startup, live migration or deployment was performed.

The first CI integration run exposed a missing MODEL_RUNTIME retention-class mapping plus stale
historical migration lists and a five-artifact error-message expectation. MODEL_RUNTIME is now
classified as outcome-bearing evidence (READER_SPEECH, like effect receipts), not disposable
diagnostics. Exact migration lists include 0049 before 0050; the incomplete-artifact refusal remains
required. These corrections do not disable or loosen CI gates.
