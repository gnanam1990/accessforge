# Retained diagnosis delivery — implementation in progress

The missing core path is retained run evidence → diagnosis → immutable finding occurrence →
reviewer-visible explanation/repair brief. This branch starts that connection; it is not yet a
completed diagnosis module, deployed model integration or physical-reader acceptance result.

`diagnosis.projection.prepare` now loads the original completed FAIL/INCONCLUSIVE evaluation and
its exact attempt. It reuses the verifier's five-artifact readback/integrity checks and compares the
artifact identities with the original evaluation snapshot. Protected descriptions come from the
frozen assertion contract, and conditions come from the original verifier snapshot, not caller
JSON. Reader observations carry bounded recorded text only when capture is known and unredacted;
unknown/redacted observations remain explicitly marked without fabricated announcements.

The privately provisioned authorized source scope is measured before and after narrow excerpt
reads. The clean commit and tree must match the sealed manifest, and the existing frozen reader
checks each file digest/path/line range and its budget. The result binds the evaluation, manifest
and complete projection digests, without granting execution or changing run/finding status.

This is a trusted internal assembly function, not a public unauthenticated API. The host must own
the source scope and workspace transaction. No model was invoked, no finding was created, and no
actual source intake or reader was run while implementing it. Changed-file Ruff/mypy checks pass;
scoped synthetic assembly regressions are authored for CI, not locally executed. Real retained
artifact/source integration coverage remains part of completing the delivery path.

Normal review corrections: preparation requires one selected assertion and exposes only that
assertion to the worker, while preserving the full original evaluation digest. Another false
assertion cannot lend its condition to the requested one. Entitlement configuration and admissions
now take the same workspace lock. The worker reports provider entry only after successful agent
construction and the last pre-call cancellation check; known pre-call failures release their hold
as NOT_CALLED rather than indefinitely reserving capacity for a call that never began.

## Durable delivery added

Migration 0039 and `accessforge_persistence.diagnoses` retain immutable diagnosis revisions, with
operation/request identity and explicit predecessor linkage. Grouping binds project, journey,
assertion contract, assertion ID and component path; every run keeps its own finding occurrence.
There is no cross-journey merge based on generic accessibility terminology or model-written prose.
New analysis never silently overwrites an original or changes an existing finding's status.

The initial finding status is derived from the original evaluation and frozen assertion condition:
INCONCLUSIVE always creates CANDIDATE, regardless of model support wording. REPRODUCED requires
FAIL, the selected assertion FALSE and validated source-linked support. The generic finding summary
contains no model-derived text. Original hypothesis, alternatives, uncertainty and repair brief stay
in the independently labelled diagnosis payload, separate from machine outcomes and human opinions.

`diagnosis.delivery.deliver` requires a current requester with run-request and evidence-read
permission, prepares original retained inputs, invokes the existing bounded diagnosis worker outside
database locks, then rechecks permission and all input identities before retention. A committed
matching operation is replayed without another model call. A durable STARTED reservation now commits
before worker invocation; concurrent duplicates cannot invoke another worker for that operation.
Provider SDK retries within one worker invocation are still controlled by its existing profile.
Crashes leave STARTED holds; interrupted calls settle as UNCONFIRMED, never automatically replayed.
Successful retention and RECORDED settlement commit together. Cancellation known to precede worker
entry records NOT_CALLED and releases capacity, without making the operation ID reusable.

Workspace token admission includes conservative reservations separately from measured/estimated
consumption. The current worker does not expose independently verified token usage: completed calls
record UNAVAILABLE usage and hold their configured allowance for the rolling window. STARTED and
UNCONFIRMED holds do not silently expire. Settings expose reserved capacity in its own labelled
column using the existing keyboard-scrollable semantic table. This is not measured provider use or
a hard financial cap; provider-usage reconciliation and an operator reconciliation workflow remain
pending. No allowance is raised automatically and no paid provider was invoked during development.

The existing findings GET now returns a bounded diagnosis history with explicit truncation and
no-store semantics. When any source artifact is marked deleted, its derived diagnosis payloads are
cleared in the same transaction. Digests and provenance remain as tombstones; updates cannot restore
deleted text. No actual user data was erased while implementing this behavior.

The existing real-PostgreSQL stopped-artifact CI fixture now checks candidate admission, operation
replay/conflict, explicit follow-up, API history, cross-workspace isolation, immutability and
derived-text deletion. The forward-migration boundary includes 0039. These additions have only
changed-file Ruff/mypy validation locally; fresh CI is required. The earlier projection-only head
0a73231 passed all applicable CI in run 34746324460.

CI-only budget regressions cover concurrent duplicate and quota races, unknown usage, conservative
hold expiry, permanent operation identity and workspace isolation. Local validation is limited to
changed-file Ruff/mypy, web production build and diff checks; no local full test suites were run.

Next: provider usage/reconciliation, authorized operator request delivery, reviewer diagnosis UI,
and real retained source/model/reader acceptance proof. No model invocation,
physical reader run, deployment or real finding creation was performed in this implementation turn.
