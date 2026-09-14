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

## One-shot local create reservation

Migration 0068 adds an immutable, forced-RLS publication-intent tombstone. The trusted
`github_publication_intent.reserve_publication` service locks and rechecks the current owner,
original stored preview, current reconstructed evidence/revisions and exact GITHUB_PUBLISH
approval (same approving user, scope, target, digest, revision, expiry and revocation). Intent
and audit commit atomically. It returns only after commit and performs no remote call.

There is one create slot per workspace/App/repository/run, independent of preview and connection
replacement. A second request never gets the first request's return value as a new dispatch
grant. A newly reviewed preview does not reopen this slot. The minimal UUID/ID/digest tombstone
is not cascaded from deletable run or preview evidence; direct update/delete is refused and only
workspace deletion removes it. Workspace deletion is therefore not remote deletion/reconciliation.

An intent means remote outcome UNKNOWN, not SENT or CONFIRMED. It has no expiring lease, automatic
retry or resume path. A failed/lost commit response requires inspecting durable state, never
repeating a create. Restoring a backup revokes publication approvals, but cannot prove what was
published after the snapshot. This reservation alone is NOT sufficient outbound authority:
the future controller must compose fresh remote access, retained-byte verification, current
local approval revalidation, one outbound call and exact response/ambiguous-outcome reconciliation.
No background worker or public route dispatches these rows.

Five grouped real-PostgreSQL preview/approval cases now include four concurrent reservers with
exactly one committed winner/audit, reconnect visibility, RLS, immutable records, wrong-digest
and revoked-consent refusals, and refusal to create again through a new approved preview.
Together with 38 forward-migration cases, 43 focused checks passed on disposable databases.
Ruff and strict mypy passed across 388 files. No live migration or GitHub publication occurred.

### Lost local response recovery

`read_publication_state` reads a durable reservation using the original request's preview ID;
the caller need not know the intent ID lost with the response. A later preview for the same
workspace/App/repository/run resolves to the original slot after its stored integrity is checked.
Only original intent ID, original preview ID/digest and timestamp are returned, not payloads,
tokens or new authority. A current workspace owner/session is required. Disconnected bindings
or revoked publication approvals do not hide historical ambiguity from an authorized owner.

Both RECORDED and NOT_OBSERVED have remote outcome UNKNOWN and retry_allowed=false. A missing
local row is not proof that no write happened: a concurrent commit, restore or workspace deletion
can make the local view incomplete. No remote receipt, lease renewal, new intent, audit mutation
or publication occurs in this read path. Remote outcome reconciliation is still separate work.

Next: outbound controller, retained-byte revalidation and remote ambiguous-response reconciliation.
Actual outbound checks still require separately scoped credentials and explicit GITHUB_PUBLISH.

Protocol reference: [GitHub check-run API](https://docs.github.com/en/rest/checks/runs).
