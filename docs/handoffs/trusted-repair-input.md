# Trusted repair-input projection

The repair worker now has a read-only input producer tied to existing retained records. It accepts
only canonical workspace/finding/diagnosis/requester IDs and an operator-configured repository map,
not uploaded diagnosis text, source contents, source digests or an author-selected base.

Current owner/maintainer project-configure and evidence-read membership, active repository authority,
repair surface, open finding, completed original FAIL/INCONCLUSIVE run, sealed manifest and source
snapshot are joined under workspace isolation. The original immutable evaluation, accepted attempt
and lease epoch must agree. The existing retained-artifact verifier checks all five promoted artifacts
against original producer streams and stored object bytes. The finding's own assertion must be FALSE;
an unrelated failed assertion is not sufficient.

Artifact locks precede the diagnosis lock to match evidence-retirement ordering. The selected diagnosis
must be retained, unsuperseded, digest-correct and bound to that evaluation/attempt. A supported analysis
and non-stopped brief are required. The Git object broker verifies the original commit, full tree and
archive without checkout, hooks, scripts or fetch. Complete allowed files and regular modes feed the
worker; unrelated repository files do not enter model input. Manifest source identities are checked
against both persisted source columns and measured Git objects. Dirty snapshots remain refused.

The prepared binding includes requester, original run/evaluation, project/source snapshot, archive,
repair-surface revision and the complete worker input. A coordinator must prepare again after model
work and compare this binding before persisting a proposal. This digest is not an authorization token
or permission to invoke a model. Transactions must close before an external model call.

The orchestrator now explicitly depends on the existing build-worker source broker; no duplicate
working-tree reader was added. Local validation uses changed-file Ruff/mypy, dependency lock generation
and diff checks. Synthetic CI-only port fixtures cover binding, retention order, denied membership,
missing baseline/evidence/diagnosis, wrong source and cancellation. They were authored, not locally run,
and do not establish real database-query or end-to-end model delivery acceptance.

Still missing: human billable repair request, durable invocation reservation/recovery, post-call
rechecks and PROPOSED persistence. No real source preparation, provider call, candidate execution,
reader startup, permission change or deployment was performed during this implementation.
