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

Next: fresh remote repository/commit verification, durable exact preview/approval,
stale-state revalidation, durable publication intent and ambiguous-response reconciliation.
Actual outbound checks still require separately scoped credentials and explicit GITHUB_PUBLISH.

Protocol reference: [GitHub check-run API](https://docs.github.com/en/rest/checks/runs).
