# Local GitHub check preview renderer

`github_check_preview` renders a deterministic local proposed check-create request. It performs
no HTTP, credential access, publication, database writes or approval issuance. It is a computation
component, not a public endpoint and not a verifier of caller-provided results.

`CheckFacts` requires exact workspace/binding/run UUIDs, source commit hash (not a branch), manifest,
journey and profile digests, and an admissible run-status/outcome pair. Completed results require
an original evaluation digest and an execution-started fact. A digest's shape does not establish
its provenance: the next service layer must reconstruct these inputs from authorized original
stored records and verify the target repository/project relationship. No caller may submit
arbitrary facts to produce an approved public PASS.

## Outcome mapping

| Original local state | Proposed GitHub status/conclusion |
| --- | --- |
| QUEUED | queued; no conclusion |
| LEASED / RUNNING / FINALIZING | in_progress; no conclusion |
| COMPLETED / PASS | completed / success |
| COMPLETED / FAIL | completed / failure |
| COMPLETED / INCONCLUSIVE, or INTERRUPTED | completed / action_required |
| CANCELLED | completed / cancelled |

Unknown/incomplete proof never maps to success, neutral or skipped. Every proposed summary carries
the domain's complete one-journey/one-profile PASS scope statement plus explicit human-review and
no-merge/deploy limitations. No repository text, raw reader phrase, model reason or arbitrary
details URL is interpolated into the summary.

The preview digest binds exact repository/App/installation/account identity, workspace/binding/run,
source/journey/profile/manifest, lifecycle, original evaluation digest and the complete proposed
request. An edited output dictionary must be rehashed and reapproved; no outbound consumer exists
yet. The stable external ID is a discovery hint, not a remote uniqueness or idempotency guarantee.
State changes keep that discovery ID but invalidate the exact preview digest.

Thirteen focused synthetic-fact tests passed for outcome mapping, pending conclusions, scope
statements, changed input/payload identity, original-evaluation requirement and impossible states.
Ruff and strict mypy passed across 384 files. These are local renderer tests, not stored-source
admission, current GitHub access, physical reader PASS, approval or publication evidence.

The owner-only `github_preview_service.prepare_check_preview` now reconstructs facts from a
current live session/membership, exact live repository binding, original run/seal, clean source
snapshot, matching build and integrity-checked original evaluation. Run/project/source/build/seal
rows are read under short shared locks and the binding is rechecked under its row lock. Current
project revocation or a different configured repository URL refuses. Supported local repository
association is exact `https://github.com/owner/repository` with optional `.git`; arbitrary URLs,
SSH aliases and redirects are not normalized into authority. No network call occurs.

The service compares schema-valid canonical manifest digest, stored row IDs, original source/tree
and artifact identity. Completed results require the original stored evaluation; it never invents
or recomputes an outcome. The preview additionally binds current run/project revisions. An expired
execution grant is not repurposed as a publication grant; historical evaluation reads do not start
a new run. Current object-store retention and remote commit membership are explicitly NOT proven
by this read-only service, and must be separately rechecked before any future outbound write.

Five focused real-PostgreSQL cases passed for HTTP-created original seals, queued previews and
wrong repository/disconnected binding/revoked project/session refusals. The disposable database
was dropped. An additional original `retain_bundle` → S3 → finalizer → preview composition case
has been added for CI; it has not run locally because the local object store is unavailable.
Its expected INCONCLUSIVE remains action_required, never a physical PASS. Strict mypy passed
across 385 files. No public endpoint or publication approval is enabled by this service function.

## Durable preview and separate local approval

Migration 0067 stores the canonical preview and its digest with exact workspace/binding/run
foreign keys and forced RLS. Updates and direct deletes are refused; parent run/workspace deletion
cascades local previews. A deleted target cannot be used to dispatch an old approval. Integrity is
rehashed on read rather than trusting the stored digest alone. No credentials are stored.

`github_preview_approval.store_preview` reconstructs the original authorized records and saves
the exact preview plus audit in one transaction. It does not approve it. `approve_preview` accepts
only the stored preview ID and the digest the human reviewed, rechecks current local owner/session
authority, reconstructs the current preview under the same transaction locks, and refuses any
changed project/run revision, binding, source or outcome. It then records a separate ten-minute
GITHUB_PUBLISH approval for that exact preview/digest/revision and an atomic audit entry.

A deterministic approval ID permits at most one approval per preview. Repeat calls do not renew
or un-revoke an approval; they fail and the caller must reconcile/read existing state. New authority
requires a newly created and reviewed preview. RUN_EFFECTS and PATCH_APPLY approvals are not valid
for this scope. These service functions are not exposed as public HTTP routes: CSRF/idempotency
handling, denial auditing and lost-response reconciliation still need endpoint integration.

The focused PostgreSQL path now also checks durable preview reconnect, immutable rows, digest
mismatch, project-revision staleness, fresh review, separate approval scope, cross-workspace RLS
and refusal to renew revoked decisions. Five grouped cases (including cross-workspace assertions)
and 37 forward-migration cases passed on disposable databases. No live migration or approval for this
user's repository was created; all data belongs to temporary fixtures.

No outbound function consumes the approval yet. A valid generic approval is not a single-use
publication receipt: current repository/commit permissions, evidence retention, approver role,
revocation/expiry and exact current payload must all be checked at dispatch, with a durable remote
intent and ambiguous-response reconciliation. No claim of completed publication is made here.

Backup restore reconciliation now irreversibly revokes all unrevoked GITHUB_PUBLISH approvals
across every workspace, including expired or orphaned targets. The operator report and atomic
restore audit include the count. Original previews and evaluations remain historical evidence;
restored consent is not publication authority. This does not discover remote writes that occurred
after the snapshot, so remote reconciliation remains required before any future publication.

Next: fresh remote repository/commit verification,
stale-state revalidation, durable publication intent and ambiguous-response reconciliation.
Actual outbound checks still require separately scoped credentials and explicit GITHUB_PUBLISH.

Protocol reference: [GitHub check-run API](https://docs.github.com/en/rest/checks/runs).
