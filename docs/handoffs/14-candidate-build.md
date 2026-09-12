# Module 14 continuation — isolated candidate build

**PARTIAL, local work.** Base main `fa60a6fd87afd770a708141b81d9067ef7e217dc` (PR #36).
Branch `feat/isolated-candidate-build`. This supplements `14.md`; it does not replace that
handoff's proposal/approval evidence or declare the module complete.

## Implemented so far

`apps/build-worker/` is a Python workspace member with no new third-party dependency.
Its source-preparation boundary:

- accepts only bounded, uncompressed regular-file tar input;
- rejects traversal, links, special/sparse members, duplicates, case-colliding namespaces,
  noncanonical paths, privileged modes and oversized/truncated input;
- reconstructs a canonical archive in memory rather than extracting into the user's checkout;
- verifies the persisted source content digest, the exact proposal bytes, approval identity,
  scope, workspace, target, revision, expiry and revocation;
- reapplies trusted repair-surface policy instead of trusting stored per-path ALLOWED claims;
- refuses dependency/build-policy changes pending a separately provisioned toolchain;
- preserves protected files and the immutable base while applying additions, modifications,
  deletions and allowed executable-mode changes to a fresh candidate snapshot;
- reports base content/archive, patch and candidate identities, never BUILT or VERIFIED.

The package is in the strict CI targets and workspace coverage guard. Lockfile changes add only
this local workspace member; no existing third-party version moved.

## Source-preparation checkpoint (commit 71a71be)

- 62 focused source-preparation and CI-coverage tests pass.
- Strict mypy passes across all 200 source files.
- Ruff lint and formatting pass.
- Full unit suite: 912 passed.
- Full Python runtime suite: `uv run pytest -q --tb=short` — 1,923 passed, zero failures/skips,
  58 upstream deprecation warnings, including real PostgreSQL/MinIO and restore tests.
- Live OpenAPI and generated binding drift checks pass.

That checkpoint covered actual in-memory tar parsing and deterministic candidate construction,
not containment or reader proof.

## Disposable execution primitive

`sandbox.py` now runs owned E0 build commands inside disposable Linux Docker containers. It requires
a preprovisioned digest-pinned trusted image, cgroup v2 and seccomp, checks the daemon's applied
configuration before starting, and has no host-execution fallback. It is not wired to the API or
durable patch dispatcher yet.

- Non-root UID/GID 65532, read-only root, no capabilities, no-new-privileges, no host source,
  credential, socket or device mounts, no network, no shared IPC.
- One CPU, 256 MiB memory with no additional swap, 64 PIDs, bounded descriptors, no core dumps,
  no daemon build logs, 64 MiB noexec/nosuid/nodev scratch, bounded output and wall time.
- Source enters through bounded stdin tar. Output leaves through bounded stdout tar; strict
  regular-file validation and canonical digests are computed on the supervisor side. Docker
  `cp` cannot reliably copy tmpfs; the documented `docker exec tar` path is used instead:
  https://docs.docker.com/reference/cli/docker/container/cp/
- A printed PASS or supplied hash does not replace the actual exit status or captured bytes.
  Capture does not attest to a quiescent/honest producer. Protected tests must consume the immutable
  captured artifact in a fresh boundary, never the producer's mutable workspace.
- Every attempted creation is followed by exact generated-name/ownership-label reconciliation,
  removal by immutable container ID and an absence check. Ambiguous creation or failed cleanup
  raises `CleanupUnconfirmed`; an interrupted create can materialize after an empty listing, so
  absence at that point is deliberately not reported as confirmed retirement.

The local probe toolchain is the Docker Official Node image, Node 22.23.2 Alpine, pinned to
`node@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32`.
The registry index was inspected and contains Linux amd64 and arm64 variants. Local proof used
the existing arm64 image on Colima; this is not evidence of a completed GitHub amd64 run. CI now
explicitly provisions that digest and runs the real probes without an optional/continue-on-error
gate. No additional Python dependency or paid service was added.

Eight real Docker tests passed locally: captured build bytes, host-file/secret/socket isolation,
read-only root and enforced process security/resource configuration, metadata/egress refusal,
symlink-output refusal, nonzero exit despite printed success, output flood, deadline/cancellation,
and actual tmpfs exhaustion. The deadline/cancellation cases share one test. No task-labelled
container remained after the run. These are owned synthetic Node builds, **not** the E0 reference
application toolchain, protected functional regressions, actual screen-reader proof, host-crash
recovery, memory/PID exhaustion proof or hosted hostile-tenant certification.

Focused execution/source/policy tests: 100 passed; strict mypy: 205 files clean. Full Python suite
with the pinned sandbox image provisioned: **1,964 passed, zero failures/skips**, 58 upstream
deprecation warnings (261.60 seconds). Ruff lint/format, live OpenAPI, six schema/enumeration
bindings and 74-operation generated-client drift checks also pass locally. GitHub CI and merge
are still pending for this branch.

## Committed-source broker

`source_broker.py` now reads the persisted source row under workspace RLS, requires an unrevoked
project with recorded repository authorization, and resolves the local repository from an
operator-owned project-ID mapping. Neither a request path nor an author-supplied content hash
selects its source. Dirty source rows are refused by this commit-only path; supporting dirty E0
work still requires a separately retained trusted artifact.

The broker follows raw commit/tree/blob objects, verifies their Git content addresses itself,
and recovers file modes as well as bytes. It does not use checkout or `git archive` (which can
apply export attributes). Full SHA-1 commit IDs are required by the existing source schema;
the canonical archive uses SHA-256. Git replacement refs are disabled, ambient Git variables
and credentials are not forwarded, lazy fetching and all transport protocols are disabled,
and no filters, hooks or submodules run. Missing, corrupt, oversized, malformed, symlink and
gitlink inputs fail closed under byte/member/time limits. An unsupported Git binary fails rather
than falling back; local testing used Git 2.55.0 and its `--no-lazy-fetch` support.

The recovered mode-inclusive archive is now bound to the persisted commit. The durable build
coordinator still must select the source via the exact patch/baseline manifest, persist this
binding with the build claim, and recheck dispatch authority. The source broker by itself is not
authorization to execute and does not create BUILT/VERIFIED state.

Real owned-repository tests cover mutable checkout, executable-mode-only changes, export
attributes, filters, replacement refs, inherited Git configuration, missing objects/promisor
remotes, corrupted blob content, links/gitlinks, dirty identities and exact response framing.
Five PostgreSQL/Git integration tests cover persisted identity, cross-workspace denial even
when supplied the exact foreign ID, absent operator mapping, revoked project and dirty row.
Focused broker/process/database run: 28 passed. Strict mypy: 208 files clean. Full Python suite
with the pinned Docker probe image: **1,988 passed, zero failures/skips**, 58 upstream deprecation
warnings (144.41 seconds). Ruff lint/format and live OpenAPI/schema/client drift checks pass.

Protocol references: https://git-scm.com/docs/git-cat-file and https://git-scm.com/docs/git

## Durable attempt and connected execution

Migration 0023 adds a workspace-isolated `candidate_build_attempt` with same-workspace foreign
keys, immutable source/approval/policy/ownership identities, one attempt per patch, a worker token
and epoch, and explicit CLAIMED/DISPATCHED/BUILT/FAILED/UNKNOWN states. This is intentionally not
the generic job queue: expiry never authorizes automatic replay.

`prepare_and_claim` selects the baseline through the patch's finding/run/sealed manifest, recovers
its persisted commit, prepares the exact approved patch and atomically opens verification plus the
build claim. The original approved revision and the incremented BUILDING revision are recorded
separately. Source, repair-surface revision, patch revision/digest, canonical archive and execution
policy identities are bound before dispatch. The durable attempt UUID determines the container
name before any Docker request.

`execute_claim` commits the one-time DISPATCHED transition only after reloading current approval,
source, project, patch and policy state. Database time is refreshed after row-lock waits so a
blocked worker cannot dispatch using an earlier pre-expiry timestamp. The coordinator then calls
the actual sandbox, and persists a captured artifact digest only after exact cleanup and fresh
worker-token/epoch/lease/patch checks. Failed builds with confirmed cleanup become FAILED;
unconfirmed cleanup or an expired attempt becomes UNKNOWN/fenced. No automatic retry/resume path
exists. Unknown attempts still need an operator reconciliation workflow.

The connected PostgreSQL/Git/Docker tests perform a real approved source edit and actual Node build
over owned synthetic source, assert the original checkout is unchanged, and compare captured bytes
with the durable receipt. A second real build prints PASS but exits 23; it records FAILED with no
artifact digest. These tests fabricate the *baseline finding* to exercise dispatch; they do not
claim a real accessibility failure, reference-app build, protected regression pass or reader result.

Additional database tests cover concurrent claim winners, identity immutability, rollback, revoked
approval, changed source/project/surface/configuration, exact approval revisions, one-time dispatch,
cross-workspace denial, lease fencing and stale/unconfirmed receipts. Focused lifecycle + source
pipeline + Docker run: 109 passed. Fresh-database forward-upgrade/recovery drill: 16 passed,
including the new 0023 boundary while preserving all 0022 rate-limit assertions. Strict mypy:
210 files clean. Full Python rerun with Docker provisioned: **2,011 passed, zero failures/skips**,
58 upstream deprecation warnings (153.12 seconds). Ruff lint/format and live OpenAPI/schema/client
drift checks pass. The initial full run correctly failed three outdated forward-boundary tests;
the explicit new migration drill was added before this clean full rerun.

The earlier source-broker head `1e8ff8f0facee18d248eeaed8f7821aef3b490d1` passed all GitHub CI jobs
in run 34713664264, including the real Docker probes on Linux amd64. That is not yet CI proof for
this new durable-attempt change. PR #37 remains draft; nothing here is merged.

## Explicit daemon and isolated Docker client

Docker execution now requires an immutable `DaemonBinding`: an operator-selected canonical local
Unix socket plus its observed daemon ID. No ambient-context discovery or remote TCP/SSH fallback is
accepted. Every command carries an explicit `--host`, a fresh empty `--config` directory, and only
a minimal PATH environment. This also prevents the Docker client's automatic proxy-credential
injection from user configuration; it does not merely remove DOCKER_HOST from the environment.
Reference: https://docs.docker.com/reference/cli/docker/

Daemon identity is checked before execution, after creation, and before/after cleanup (including
the absent-container path). A replacement daemon cannot turn an empty listing into cleanup proof.
The binding is part of the version-2 execution policy, persisted immutably in migration 0024's
attempt columns, checked again at dispatch, and matched to the returned receipt. Recovery still
needs to operate on this exact binding, not today's default context.

Migration 0024 does not invent historical provenance. Unfinished legacy attempts without a binding
become UNKNOWN with an incremented fence epoch and MISSING_DAEMON_BINDING; existing built receipts
remain historical and unbound. The fresh-database drill seeds actual pre-0024 CLAIMED, DISPATCHED
and BUILT rows and verifies these distinct outcomes. All prior 0023/0022 assertions remain covered.

New tests cover endpoint validation, empty client configuration, restricted CLI environment,
daemon replacement before/after an absent listing, changed persisted daemon identity, and a real
Docker build with deliberately hostile ambient context/host/TLS/proxy settings. The real test uses
synthetic credential canaries and does not modify the user's Docker context or configuration.
Fresh forward-migration/recovery drill: 17 passed. Full Python suite with explicit endpoint and
pinned toolchain: **2,023 passed, zero failures/skips**, 58 upstream deprecation warnings
(155.25 seconds). Strict mypy: 210 files clean; Ruff and OpenAPI/schema/client drift checks pass.

Earlier durable-pipeline head `41427b5c1e5ba8144ad57624a0b579d00208bbe3` passed all GitHub CI in run
34714720918. The new endpoint-binding head requires its own CI; PR #37 stays draft.

## Durable candidate archives and process identity

Migration 0025 adds workspace-isolated, immutable process provenance and archive identity.
After Docker returns an immutable container ID and configuration is inspected, the coordinator
commits its actual resolved image ID/platform and explicit daemon binding before starting source
execution. Failure to record this receipt prevents execution and follows exact-container cleanup.
This receipt means creation was observed, not that source execution succeeded.

Successful output is stored through the existing S3-compatible object store in a separate
`workspaces/{workspace}/candidate-builds/{build}/archives/{sha256}` namespace. Executable tar
archives are not added to the transcript/evidence MIME allowlist. The supervisor commits a
QUARANTINED upload intent first, writes its own canonical bytes, then performs a size-bounded
read-back and SHA-256 comparison. Archive promotion and BUILT occur in one database transaction
only after fresh worker/epoch/lease and patch checks. A digest-only finish call is now refused.

The retained reader derives the namespace independently, enforces workspace RLS and BUILT/
RETAINED states, rechecks the bounded bytes on every read, and reparses the strict archive.
Substitution, missing objects and cross-workspace reads cannot return an available candidate.
Upload failure or fencing after upload leaves a quarantined intent, never a BUILT receipt.
Raw producer logs are not retained here; stdout/stderr digests accompany the archive.

Restore reconciliation fences every CLAIMED/DISPATCHED candidate, including unexpired leases,
as UNKNOWN with a new epoch and RESTORED_DATABASE. It does not assert container retirement or
redispatch. The existing backup enumerates all bucket keys (including this new namespace);
its object reads are now bounded before allocation. The dedicated candidate-byte encrypted
backup/restore drill is described below. Archive retention/deletion policy integration is outstanding.
Historical BUILT rows remain historical: migration does not invent process receipts or bytes.

The new connected tests use real Git, PostgreSQL, Docker and S3-compatible storage. Injected
upload outages, post-upload fencing and equal-size object substitution test failure paths around
real storage operations. Successful builds prove read-back, durable resolved process identity,
immutability, cross-workspace denial and refusal after object tampering. Baseline findings remain
synthetic; none of these tests claims actual E0 accessibility repair or reader verification.

Preceding endpoint-binding head `4a868c5767ab94e05e57616d6b76a7e2128d89ec` passed all GitHub CI
in run 34715395960. This retention continuation passed the full Python suite with the explicit
endpoint and pinned toolchain: **2,033 passed, zero failures/skips**, 58 upstream deprecation
warnings (163.94 seconds). Strict mypy: 211 files clean. Ruff lint/format and live OpenAPI,
six schema/enumeration bindings and 74-operation client drift checks pass. The full suite includes
the fresh 0025 upgrade drill, retained 0024/0023 assertions, and unexpired-claim restore fencing.
The earlier focused source/patch/migration/restore run passed 144 tests before the last new cases.
The new committed head still requires its own GitHub CI; PR #37 remains draft and unmerged.

## Actual candidate-byte encrypted recovery drill

The connected source test now runs the actual Node candidate build, retains its canonical output,
invokes the operator's backup script with evidence included, removes the source object's bytes,
and invokes the restore script into a fresh database and a separately generated bucket. It then
uses the application-role retained reader to compare recovered bytes and checks the original
process/archive records. Nothing is restored into the source bucket or source database.

A second case starts from actual uploaded but substituted bytes: the upload remains quarantined
and the worker claim is still DISPATCHED. Restoring that archive preserves quarantine but fences
the claim as UNKNOWN with a new epoch and RESTORED_DATABASE. Neither restored case is redispatched;
the retained case is still a build receipt, not a VERIFIED repair. Missing/source-bucket choices
are refused before any target tables exist. Owned drill databases and buckets are removed afterward.

The restore script now verifies each uploaded object's exact bytes with a bounded read-back
before restoring PostgreSQL. Storage errors return failure with the database untouched and warn
that partial objects may remain in the isolated bucket. Real S3 fault-injection cases substitute
equal-size, truncated, oversized or missing objects after upload; none is accepted.

This drill exposed an older database defect: the terminal-run immutability trigger returned NEW
for a nonterminal DELETE. NEW is NULL in that operation, so it silently cancelled deletion even
during a workspace FK cascade. The parent disappeared but the run survived; the resulting dump
could not restore its foreign key. Migration 0026 returns OLD for a nonterminal DELETE while still
rejecting UPDATE/DELETE of all terminal states. The forward drill demonstrates the old orphan
inside a rolled-back transaction, proves the cascade after migration, and preserves terminal guards.

Migration does not fabricate historical workspaces or delete existing orphaned records. Backup
preflight now refuses known orphan runs using an all-workspace connection and directs the operator
to recover parent records from trusted history. That read-only check detects this specific legacy
defect; it is not a claim that every possible form of database corruption has been audited.

Preceding retention head `550674c68276bbb62e1837f80a0482fdadd23710` passed all GitHub CI in
run 34716225101. The source/forward-migration/backup focused run passed 50 tests before the final
read-back fault cases were added. The final full Python suite passed **2,043 tests, zero failures/
skips**, 58 upstream deprecation warnings (165.86 seconds), using the explicit Docker endpoint
and pinned toolchain. Strict mypy: 211 files clean; Ruff and OpenAPI/schema/client drift checks pass.

Local evidence-first review covered this recovery delta against 550674c, including the failing
pre-migration cascade, terminal-state preservation, restored quarantine fencing, actual byte
recovery and post-upload read-back faults. No evidence-backed defects remain in that reviewed
scope. It is an internal change review, not an independent security audit or approval of the
entire draft PR. No third-party integration changed. New-head GitHub CI remains a separate gate.

## Source-class expiry, permanent tombstones and storage binding

The trusted `retire_expired_candidate` operation now uses the workspace's SOURCE_SNAPSHOT policy
for source-derived candidate archives. Missing policy classes fail closed. The retained reader
checks expiry before and after its bounded read; build/archive locks order the read before a
concurrent retirement intent. No new public endpoint or unattended sweep is enabled by this change.

Migration 0027 records a create-only upload protocol and a durable retirement intent. The upload
intent is consumed once, and candidate PUTs use `If-None-Match: *`, including the candidate-object
restore path. Expiry first commits retirement intent, making reads/promotion unavailable, and
fences unfinished attempts as UNKNOWN. It then replaces the active-store payload with a permanent
empty object, verifies that empty body, and commits DELETED plus a completion receipt. A response
lost after the object write leaves a pending intent; repeating retirement completes the same
operation. Historical build/process/digest records are not rewritten into a different build result.

The key must remain present. Deleting it would permit a delayed create-only upload to recreate
bytes. A zero-byte tombstone blocks those uploads; it is not an S3 delete marker. The primitive
refuses versioned or suspended-versioning buckets, any bucket lifecycle/replication configuration,
and unreadable configuration, both before and after the write. Operators must preserve those
constraints and exclude tombstones from any external cleanup. Runtime retirement does not call
DeleteObject. This is active-store payload retirement, **not erasure of prior backups, exported
copies, provider replicas outside the declared store, or a guarantee against a privileged operator
overwriting data**. Automatic sweep dispatch and documented backup-expiry enforcement remain work.

Protocol reference: [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
The existing boto3/S3 integration now needs conditional PutObject plus GetObject, GetBucketVersioning,
GetLifecycleConfiguration and GetReplicationConfiguration permissions for retirement. No new SDK,
provider, paid service or cloud deployment was added. Tests use disposable local MinIO buckets.

The internal review found a reachable storage-identity flaw in the first local draft: retiring into
the wrong bucket reported success while 10,240 original bytes survived. Migration 0028 and the
corrected paths bind capture immutably to the exact logical endpoint/bucket and reject mismatched
stores before retirement intent or storage I/O. An isolated restore appends an immutable,
revisioned location record only for transferred objects, verifies retained bytes against database
digests (or verifies an empty deleted tombstone), and preserves original capture provenance.
Location registration and restore reconciliation commit together. A restored reader cannot
silently fall back to the original bucket. Legacy records with unknown protocol/location remain
unbound and require operator reconciliation; no historical safety claim is fabricated.

Tests cover expiry, wrong workspace/store refusal, lost retirement responses, delayed uploads
before and after PUT, late promotion fencing, permanent marker restore, source-bucket fallback
refusal, immutable location/retirement records, and real versioning/lifecycle/permission failures.
The latest focused source/migration run passed 51 tests. A separate disposable review probe raced
20 actual uploads against retirement: initial uploads won 10 and retirement won 10; all 20 final
objects were empty and all 20 later rewrites were refused. Its generated bucket was removed.
The tests do not claim control over an arbitrary remote provider's internal in-flight requests.

Recovery head `35deffdb08e01ebcba90f193e8fe2672c3052023` passed all GitHub CI in run
34716804109. The final retirement/location full Python suite passed **2,053 tests, zero failures/
skips**, 58 upstream deprecation warnings (180.88 seconds). Strict mypy: 211 files clean. Ruff
lint/format and OpenAPI/schema/74-operation client drift checks pass. The internal review's
wrong-store defect was reproduced and fixed before commit; no evidence-backed defects remain in
this reviewed delta. This is not an independent security audit or full draft-PR approval, and
new-head GitHub CI remains a separate gate. No formal forge review was published.

## Actual owned-reference packaging checkpoint

The reference-app toolchain is now provisioned from a four-file trusted context, with Docker
Official Python 3.13.15 slim-bookworm pinned to
`python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e`.
Hatchling 1.32.0 and its transitive build dependencies are hash-locked separately; runtime
requirements are an exact frozen export of the existing workspace lock. Installation permits
only prebuilt wheels, hashes and no dependency resolution. Provisioning is networked trusted
operator work; target source is not in its context. The actual candidate frontend uses offline
PEP 517 wheel building inside the existing restricted sandbox.

Local arm64 provisioning returned immutable image
`sha256:207326fe923c570016e21864aee26842dbef19c8e48243f465613c301c0a441c`.
Dispatch now accepts a full immutable local image ID as well as a pinned registry digest;
mutable tags and short IDs remain refused. The toolchain receipt includes its canonical context
digest and observed daemon binding. No image was published to a registry.

The integration fixture derives a standalone repository from exact committed
`fixtures/reference-app` package bytes, records that parent commit/subdirectory provenance, then
uses the real persisted Git/source/approval/claim pipeline. Its baseline finding and approved
comment-only edit are explicitly synthetic build inputs, not an actual reproduced reader finding.
It creates and retains the real Python wheel, compares every packaged source member against the
approved candidate, and imports that captured wheel in a fresh no-network container. No candidate
module is imported or installed on the host. Protected backend source bytes are unchanged, but
that equality and the import smoke do NOT establish protected functional/security regression proof.

CI now provisions its own platform toolchain, exports its immutable ID to the tests, audits the
backend hash lock and checks runtime-lock drift. Missing reference provisioning fails in CI.
Focused provisioning/image/CI/actual-reference checks passed 20 tests. The complete Python suite
with both actual provisioned toolchains passed **2,060 tests, zero failures/skips**, with 58 upstream
deprecation warnings in 180.22 seconds. Strict mypy passed 215 files; Ruff lint/format and live
OpenAPI/six-schema/74-operation client drift checks pass. Both the six-package backend lock audit
and the 96-package workspace audit report no known vulnerabilities. Retirement/location head
`8122be6360bca9ae007a75e39e8e63e67dd0bc2a` passed GitHub CI run 34717972253; the new Python
toolchain's amd64 CI remains a separate gate. No new migrations or public API were needed.

Important follow-up: `template_digest` currently hashes rendered frontend HTML. A real frontend
repair changes that digest, so logical fixture identity must be separated from the patchable UI
before claiming a matched baseline/candidate reader comparison. The comment-only packaging test
does not solve or conceal this boundary.

## Required next work

### Protected reference-app regressions checkpoint

`reference_regressions.py` now exercises captured wheels through actual HTTP and PostgreSQL,
without importing the candidate on the host or putting the harness in candidate-writable paths.
The supervisor creates a task-specific PostgreSQL 17 container pinned to
`postgres@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675`.
It has network=none; separate candidate and trusted-driver containers share only that exact
private loopback network namespace. No ports are published, no host source/credential/socket
mounts are used, and each role has resource limits, readonly root, no capabilities and no IPC.
The image's declared data volume is shadowed by bounded tmpfs rather than allocating a host volume.

The trusted supervisor creates the fixture schema as database administrator. Candidate credentials
permit only SELECT/INSERT/DELETE/TRUNCATE on the two fixture tables, not schema/role administration.
The administrator password/socket/filesystem and test-driver process remain outside the candidate.
The trusted startup explicitly calls `create_app` on the captured wheel and does not invoke the
candidate's schema initialization; protected schema changes are not accessibility repair scope.
Actual driver probes must observe permission refusal for DROP TABLE, CREATE TABLE and reading
pg_authid. Metadata/public TCP attempts must fail on the shared network-none namespace.

Checks cover fixture creation, required-field/email/category/description validation, no durable
write on invalid input, observer/setup identity separation, unauthorized fixture/reset no-write
behavior, exact successful backend fields, duplicate submission conflict, unknown fixture refusal,
and durability across a genuinely new candidate container/filesystem using the same captured wheel.
The supervisor reads the real database directly; an application receipt endpoint is not its oracle.
Before its final read it kills the candidate, revokes login, terminates outstanding candidate DB
backends and observes none remaining. This closes the late-write window after merely killing a
client. Exact task-owned cleanup is required before returning any result; cancellation after actual
PostgreSQL startup is exercised, and ambiguous creation/cleanup still raises UNKNOWN-style refusal.

Regression tests build real approved-template sabotage variants: removing validation, bypassing
token comparisons, and persisting invalid data while still returning HTTP 422. The protected
checks must reject each at its relevant boundary. Tests never execute those variants on the host.
These are intentional local test mutations, not changes to the reference-app checkout.

This result remains an in-memory trusted primitive. Durable candidate-run/lease/epoch registration,
crash recovery, persisted regression attestation and actual matched-reader scheduling remain
required. The existing verification gate stays false; no build or regression result alone makes
a repair VERIFIED. No hosted arbitrary-tenant support or external deployment is added.

Networking reference: [Docker container network sharing](https://docs.docker.com/engine/network/#container-networks).

Validation: full Python suite with the actual Python and PostgreSQL runtime images passed
**2,063 tests, zero failures/skips**, 58 upstream deprecation warnings, in 201.17 seconds.
Strict mypy: 216 files. Ruff lint/format, live OpenAPI, six schema/enumeration bindings and
74-operation client drift checks pass. No task regression containers remained after the suite.
Packaging head `c50f513bca54853cab33bc89dc2ad8dcceddc557` passed all GitHub CI in run
34718869094, including its actual amd64 wheel build. The new regression-runtime head must pass
its own CI. Internal review is not an independent security audit or complete draft-PR approval.

### Remaining integration

1. Integrate the trusted build/retirement operations into bounded operator/job dispatch and implement
   legacy unbound-store reconciliation plus documented backup expiry. Policy-based active-store
   retirement and the candidate-byte encrypted backup/restore drill are implemented. No public
   build endpoint or unattended sweep is exposed.
   Retained dirty-artifact intake remains required if dirty E0 candidates are supported.
2. Implement operator reconciliation and durable crash/recovery tests for UNKNOWN attempts using
   the pre-recorded task and explicit daemon binding. Fencing and endpoint binding are implemented;
   safe reconciliation/resumption is not. In particular, an absent container alone cannot prove an
   interrupted create request will not materialize later. Use the now-persisted creation receipt
   to distinguish observed absence from confirmed retirement; never automatically retry UNKNOWN.
3. Connect the now-proven reference-app package toolchain to bounded operator E0 dispatch and
   separate logical fixture identity from repairable presentation. Docker daemon access is
   supervisor authority, never an author-selectable endpoint. Packaging/import proof is available;
   end-to-end reader repair proof is not.
4. Bind the implemented protected HTTP/database regression runner to durable candidate/run/lease
   provenance and independent verification. The captured artifact is hashed independently;
   repository stdout and self-reported identities remain non-authoritative.
5. Exercise real containment canaries, metadata/egress refusal, resource exhaustion, cancellation,
   crash, durable recovery and exact task-owned cleanup. No blind retry after ambiguous execution.
6. Connect the proven build to module 15's fresh matched actual-reader run. VERIFIED remains
   unavailable without complete trusted evidence and protected regression results.

No candidate was published, no hosted arbitrary-source support is enabled, no model call or paid
service was used, and the user checkout was not used as a candidate workspace.
