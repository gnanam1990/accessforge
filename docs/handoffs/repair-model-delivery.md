# Explicit repair-model delivery

The stored human repair request now connects to trusted input preparation, a durable shared-budget
reservation, the constrained model worker, post-call source/evidence/authority rechecks and the
existing PROPOSED patch service. No request handler or application startup dispatches it.

The operator entry point is:

```text
python -m accessforge_orchestrator.repair.operator --workspace-id WORKSPACE_UUID --request-id REQUEST_UUID --project-id PROJECT_UUID --repository OPERATOR_REPOSITORY --allow-billable-model-call
```

The host supplies database, evidence-store and provider configuration. Uppercase placeholders are
not executable identities. The explicit cost flag is required before repository/configuration
access; the service independently requires current stored consent. The repository map is private
operator configuration and cannot choose another base or requester. This command was not run on
user data or with real provider credentials during implementation.

STARTED commits before model construction. The operation digest binds human scope, full prepared
identity and the exact worker/model-profile digest. No transaction remains open across the model
call. Preparation locks artifacts before diagnosis rows; consent is rechecked after preparation,
avoiding a diagnosis-first/artifact-second lock inversion. After the call, preparation runs again
and must match the original binding before any proposal is inserted.

Proposal, immutable migration-0044 delivery receipt and invocation settlement commit together.
Request inspection/recovery now includes that receipt and its original patch ID/digest. A known
delivery returns readback without a new model call, even after consent expiry, subject to current
read membership. STARTED, UNCONFIRMED and NOT_CALLED without a receipt do not invoke again.
Local construction/cancellation before provider entry settles NOT_CALLED; uncertain post-entry
failures retain the budget hold as UNCONFIRMED. A lost commit response may mean RECORDED already
committed: settlement cannot overwrite it, and readback recovers the original patch.

A bounded known result without a reviewable draft can record NO_PROPOSAL. PROPOSED means stored
unverified source changes and rationale/uncertainty, not PATCH_APPLY approval, candidate execution,
functional correctness or VERIFIED. Model text appears only in the existing proposal records;
the delivery receipt and operator stdout contain identity metadata, not source/credential copies.
Source comparison retention remains the explicit comparison workflow; this delivery does not fake
an original-source comparison, approve the patch or run a build.

Changed-file Ruff/mypy and diff checks are local validation; local test suites were not run.
CI-only orchestration fixtures model single reservation, post-call scope refusal, write rollback,
lost-commit readback and pre-provider failure. A real-DB savepoint fixture checks exact receipt/patch
binding, immutable receipt, unsettled-result refusal and rollback of all three records together.
These use synthetic model/source data, not live provider or end-to-end repair proof.

Still required: browser repair-request/recovery controls, real authorized provider/source acceptance,
provider-usage reconciliation, actual protected-regression/native-reader matched verification and
the remaining release/deployment gates. No paid call, reader startup or deployment occurred here.
