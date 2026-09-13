# Constrained repair-generation worker

Module 14 now has an internal structured proposal generator over complete original files and a
SOURCE_LINKED diagnosis repair brief. It does not expose a public model endpoint, construct an
authorized input from caller uploads, or persist a patch by itself.

Inputs include workspace/finding/diagnosis identities, canonical diagnosis-analysis digest,
original manifest/tree/commit, current configured repair paths and full bounded original files.
The source-location file digest must match the full file; partial excerpts cannot masquerade as
complete replacements. Brief files and source files must agree exactly. Unsupported diagnosis,
stop recommendations, duplicate/case-colliding paths, protected paths, unsupported modes, binary
text and exceeded byte/context budgets refuse before model invocation. A matching digest proves
internal consistency, not repository authorization: the trusted delivery layer must supply it.

The pinned Strands model has no tools, directory discovery, memory, session, background task or
checkpoint manager. One model retry-strategy attempt, a one-turn invocation limit, token budgets,
timeout and cancellation bound the worker. No model was called during this implementation.
The provider-entry callback separates local construction failure from potentially billable entry;
an external coordinator must still reserve/reconcile usage. Token settings are not a financial cap.

Structured output names exact original-file digests and full replacement text or deletion, plus
rationale and uncertainty. Post-validation rejects foreign paths, digest mismatch, duplicate or
unchanged files, non-text/oversized content and protected scope. Dependency/build-description paths
stay explicitly separately reviewed; the model cannot acknowledge them or issue PATCH_APPLY.
The result identity binds both exact input and model profile. PROPOSAL_READY means an in-memory
draft only, not PROPOSED persistence, approval, application, functional correctness or VERIFIED.

This initial generation surface repairs existing complete files; it does not create new files
whose absence has not been established by a trusted source broker. The model cannot prove that a
semantic change preserves validation or task behavior: normal human review and independent
protected regressions remain required, with no automatic promotion based on model wording.

Changed-file Ruff/mypy and diff checks are local validation. Synthetic CI-only fixtures cover
valid bounded output, scope/digest mismatch, no-op/duplicate refusal, cancelled/missing output,
pre-invocation cancellation/context refusal and stopped/unbound input. No local test suite ran.

Still required for the user-facing end state: a current-authority retained-diagnosis/source-broker
projection, explicit billable request/usage reservation, durable operation identity and uncertain
call recovery, post-model rechecks, and PROPOSED persistence through the existing patch service.
No model credentials, host permissions, user checkout, build daemon, reader or deployment changed.
