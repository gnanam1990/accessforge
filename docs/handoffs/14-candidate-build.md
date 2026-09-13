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

### Durable regression execution checkpoint

Migration 0029 adds workspace-RLS protected regression attempts and process records. The trusted
coordinator reads actual retained bytes, binds their digest to the completed build and its original
image/daemon, rechecks patch revision/digest/approval/source/repair surface/retention, and commits
one claim and one dispatch. Claim inputs, ownership and terminal outcomes are immutable; lease
expiry increments epoch and records UNKNOWN instead of retrying.

The task ID determines role names before Docker side effects. Each plan commits before creation;
each actual immutable process/image receipt commits before start. Exact removal facts are recorded
after cleanup, including observations arriving after a fence. PASSED requires all four roles
removed and matching captured artifact/policy/check/process records. Policy identity binds the
executing harness bytecode/typed constants plus schema/HTTP driver/images/daemon/limits, not a
mutable source file read after execution. A raw marshal-based first draft changed its hash during
a healthy real run; deterministic code-attribute hashing fixed the demonstrated rejection.

Failed execution with confirmed cleanup is FAILED; unconfirmed creation/cleanup remains UNKNOWN.
Confirmed cancellation and other interrupted observation use distinct failure codes. No stale
worker can publish after fencing, no duplicate dispatch consumes another execution, and no caller
supplies a verification conclusion. The existing module 15 gate remains closed: these are durable
build-linked protected results, not fresh matched-reader run attestations.

Restore now fences active regression claims to UNKNOWN/RESTORED_DATABASE and reports their count.
The encrypted backup and genuinely separate database/bucket restore drill includes a committed
regression dispatch intent and proves it cannot be redispatched after restore. This preserves
history; it does not prove a hard-crashed worker's live container was stopped. Operator
reconciliation of that original daemon remains required.

Focused migration/source/runtime/restore checks passed 62 tests before the additional durable
cancellation/fence cases. The final full suite passed **2,072 tests, zero failures/skips** with
58 upstream deprecation warnings in 219.61 seconds. Strict mypy passed 218 files; Ruff lint/format
and OpenAPI/six-schema/74-operation client drift checks pass. All integration tests use owned
temporary fixtures; protected specification documents remain unchanged. Regression-runtime head
`0fca82494b5b405fcced883a674200e2476234e4` passed all GitHub CI in run 34719676683.
The new durable-regression head must pass separately.

Creation observations now commit independently of permission to start: if a lease fence lands
after Docker creates a container, its ID is still recorded, activation is refused by a fresh
authority check, and its removal is retained without reopening UNKNOWN. Time-based approval and
retention are checked after blocking reads, not against a clock captured before them.

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
3. Connect the now-proven reference-app package toolchain and opt-in loopback candidate bridge
   to bounded operator E0 dispatch. The live bridge selects the exact retained artifact and
   isolated container (see below); persist that deployment binding and match the actual runtime
   to the sealed reader environment. Logical fixture identity alone is not deployment provenance.
   Docker daemon access is supervisor authority, never an author-selectable endpoint.
   End-to-end reader repair proof is not available.
4. Link the implemented durable build-bound protected regression receipts to a fresh, matched
   candidate reader run/lease and independent verification. Actual-reader evidence is not yet
   bound; repository stdout and self-reported identities remain non-authoritative.
5. Exercise real containment canaries, metadata/egress refusal, resource exhaustion, cancellation,
   crash, durable recovery and exact task-owned cleanup. No blind retry after ambiguous execution.
6. Connect the proven build to module 15's fresh matched actual-reader run. VERIFIED remains
   unavailable without complete trusted evidence and protected regression results.

No candidate was published, no hosted arbitrary-source support is enabled, no model call or paid
service was used, and the user checkout was not used as a candidate workspace.

### Versioned logical fixture, separate presentation identity

The frozen `packages/contracts/fixtures/reference-service-request-v1.json` describes the shared
service-request backend task: fields/validation, authorized setup and observation, submission
outcomes, one request per nonce, fresh nonce and restart durability. The accessible/inaccessible
presentations share this definition; changing HTML alone no longer changes its identity.
`scripts/generate_reference_fixture.py --check` is a mandatory CI drift gate for generated host
Python, standalone reference-app and TypeScript bindings. An independent golden digest is tested
in both languages. V1 semantics must not be silently edited/relabelled as the same contract.

Version: `accessforge.reference-service-request/1`.
Canonical SHA-256: `39acd4e6ff833c3f5668cbc951f541658858568bd9ba31814bfb19a318dbb6a3`.
The separate `presentation_digest` remains a diagnostic HTML hash. Candidate source/archive
digests continue binding exact presentation bytes. The journey/manifest `fixtureDigest` also
covers navigator/reset/observer values; it is NOT interchangeable with the template definition
digest. A future trusted controller must select and seal both appropriate identities.

Fixture creation declares this explicit version and stores the logical template hash. There is
no migration/backfill of historical fixture rows or run evidence. Older HTML-derived identities
remain historical; the new supervisor refuses old/missing-version declarations. A fresh reset
and seed under the new contract is required, not rewriting old evidence to make pairs match.

The contained regression harness binds the frozen definition in its policy, verifies the fixture
declaration and independently compares the actual PostgreSQL row. Protected HTTP/database behavior
checks still run: a matching marker alone is not proof. Tests build an approved presentation edit
and a malicious definition mutation in real disposable candidate containers; no target code is
imported onto the host. These synthetic candidate tests do not establish actual reader success.

Browser setup requires the trusted template digest before reset and exact candidate digest,
version, variant and bounded nonce before launch. Native HTTP transport forbids redirects, caps
decoded response bytes at 16 KiB and applies a five-second deadline through body consumption.
Injected fetch adapters remain trusted test/operator code, not candidate-selected transport.
Only the start URL enters the navigator projection; setup credentials remain supervisor-only.
This helper does not prove supplied build digests or browser deployment provenance. The module 15
attestation gate remains closed pending exact build/source/run/lease linkage and real reader proof.

The preceding durable-regression commit `0fdae5896e7ebc15f353082b10de027449ebcebb` passed all
GitHub CI jobs in run 34720879520. This continuation requires separate current-head validation.

Local checkpoint `3fe5e0d70b6f2c52ec29726d0baadcbc91ef86f5` passed the full Python suite:
**2,087 passed, zero failures/skips**, 58 upstream deprecation warnings, in 224.64 seconds.
That includes actual captured-wheel presentation repair with unchanged backend semantics and
rejection of a candidate that tampers with the fixture definition. Strict mypy: 223 files clean;
Ruff: 325 files clean; OpenAPI, six schema/enumeration bindings, 74-operation client and frozen
fixture binding drift checks pass. All Node workspace typecheck/build/test commands pass; the
final desktop suite has 56 passing tests, including real loopback HTTP redirect, oversized-body
and stalled-body refusal. No actual-reader or production deployment result is implied.

### Opt-in contained candidate browser bridge

The trusted regression coordinator can now opt into a short, owned browser capability probe.
After the captured wheel is loaded into its exact isolated process, the supervisor seeds and
independently checks the logical fixture. It opens an ephemeral IPv4 loopback socket that serves
only that nonce's form. Browser requests traverse a trusted Docker-exec HTTP driver in the
candidate's private network namespace; there are still no published container ports, host mounts
or outbound candidate network access. The candidate cannot select the upstream host or process.

Every forwarded request rechecks current durable regression authority, original daemon identity,
both immutable process IDs, running state, exact images/network and containment configuration.
Docker I/O shares a five-second request deadline. The live binding records artifact/task/origin/
nonce path/driver/candidate/image/daemon and the trusted runtime policy digest. It does **not**
pretend that this policy digest is a sealed reader-environment manifest. Neither the binding
object nor the callback can attest a reader result. No public API or default unattended dispatch
enables this hook, and there is no new path to VERIFIED.

The gateway permits only exact GET/POST form paths, rejects wrong/duplicate Host, cross-site
navigation and missing/wrong POST Origin, and refuses ambiguous Content-Length or chunked framing.
Headers are capped at 16 KiB, form bodies at 8 KiB, responses at 256 KiB, with 128 request admissions.
An absolute two-second framing timer closes slow clients. The normal lifetime is 30 seconds
(trusted maximum 60); expiry closes the listener even if the callback is still busy. Candidate
redirects and response headers are not forwarded. A fixed CSP, no-store, no-referrer and nosniff
policy accompanies form responses. Setup/reset/receipt/diagnostics and alternate fixture paths
never reach the driver through this socket; browser cookies/auth headers are discarded.

Cleanup closes the listener and joins started threads without allocating another shutdown
thread. Unconfirmed handler termination remains CleanupUnconfirmed/UNKNOWN, never a clean close.
Review reproduced a listener leak when Thread.start failed; server-thread and timer-start failure
tests now cover its correction. A second lifecycle test caught a stopped serve loop with a still
bound listener; unconditional listener close on loop exit corrected it. The interrupted first
full-suite run (222 tests before SIGINT) is not claimed as a complete validation.

The owned real-wheel tests exercise both original and presentation-repaired candidates through
the bridge, inspect the actual HTML/validation response, refuse setup access and compare the
route's artifact/process identities with the retained build. A durable fence during the callback
must prevent the next request reaching any container and preserve UNKNOWN plus exact removals.
Backend protected regressions receive a fresh fixture after the separate browser window closes.
Browser callbacks never contribute protected-check or assertion PASS evidence.

A disposable observer plugin also held the presentation-repair endpoint for native inspection:
the Codex in-app browser rendered the Service request form with labelled name/email/category/
description controls and submit button. The owned integration case completed successfully after
closing the endpoint. This was a synthetic fixture/browser-render capability proof, **not**
VoiceOver/NVDA interaction, a canonical run, a reproduced finding, or a verified repair.
The final browser empty-submit attempt did not establish an interaction result: the local
endpoint was unavailable and the browser tool refused its generated error-page navigation.
The owned preview test itself ended cleanly (1 passed in 31.51 seconds); no safety warning was
bypassed. Native browser POST/error-focus proof remains pending, separately from the successful
real HTTP POST/validation tests. Temporary preview tabs are not deployed deliverables.

This intentionally short local E0 bridge is not a production web server or a hostile-browser
certification. Python explicitly cautions against production use of its standard HTTP server:
[Python HTTP server documentation](https://docs.python.org/3/library/http.server.html).
CSP is a content restriction, not a substitute for the remaining browser/desktop isolation and
continuous-origin checks: [CSP sandbox reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox).

Final local runtime validation: **2,118 passed, zero failures/skips**, 58 upstream deprecation
warnings, in 241.83 seconds. This includes the live endpoint fence race plus all prior actual
Git/PostgreSQL/Docker/object-store/backup/restore paths. Focused gateway suite: 30 passed.
Strict mypy: 225 files; Ruff lint/format: 327 files. OpenAPI, six schema/enumeration bindings,
74-operation client and frozen fixture drift checks pass; Node workspace typecheck passes.
The preceding fixture-identity head `bdeb4f4e57c5ce1d7909af1d4dffa6b2b93f8681` passed all
GitHub CI jobs in run 34721876262. This bridge continuation requires separate current-head CI.

### Durable endpoint intent, admission and closure (continuation)

Migration 0030 adds workspace-isolated `candidate_endpoint` records and an immutable
`endpoint_required` mode on the build-bound regression claim. Historical rows default to false;
the migration preserves old PASSED results without manufacturing endpoint receipts.

The coordinator commits an exact intent before allocating a listener, commits the reserved
loopback origin and canonical binding receipt before starting request handling, and rechecks
current worker/epoch/lease/source/approval and endpoint authority before admission and every
candidate transport. The intent must match the three already observed runtime processes, original
image/daemon, retained artifact and runtime policy. It is not a sealed reader environment.

Intent and first binding are immutable. Successful publication requires a bound and cleanly CLOSED
endpoint when one was requested. Parent expiry/restore fences active endpoints UNKNOWN; a later
confirmed close can add cleanup facts but cannot reopen UNKNOWN or publish a passing result.
Restore audits/counts fenced endpoints. The encrypted candidate backup drill includes a clearly
synthetic endpoint crash-intent seed: its plan survives real backup/isolated restore, remains
UNKNOWN with no invented binding/cleanup and cannot regain live authority. That seed is not
evidence of an actually interrupted browser or listener.

Fault injection covers binding-persistence failure and a fence between binding and admission;
neither reaches the browser callback, and the reserved socket is closed. Real runtime probes
check the committed BOUND receipt before GET/POST, tenant invisibility, substituted identities,
duplicate binding, immutable endpoint mode and final CLOSED/UNKNOWN cleanup records.

Two timeout defects were reproduced and corrected during this continuation. CI 34723248738 on
`0a7aad73b635c961e019ffc8ebded4643652702a` failed the Linux expiry test: closing a descriptor
from another thread could leave the underlying listener accepting while select held it. Explicit
socket shutdown now precedes close. A disposable Linux/arm64 harness ran the actual baseline and
working gateway source against the same delayed-select case: baseline accepted a post-expiry
connection (exit 1), working source refused it (exit 0). The harness loads exact gateway/domain
source and the sandbox identity/error definitions only; it is not a full Linux integration run.
The expiry timer also now starts immediately after socket reservation, before persistence
callbacks, and expired callbacks cannot admit a handler. The same scratch callback-stall regression
failed before the fix and passed after it; the repository retains equivalent regression coverage.

Remaining: bounded E0 controller, original-daemon UNKNOWN reconciliation, exact sealed
environment/deployment/source/project/verification linkage, and fresh canonical actual-reader
run/lease/observer proof. `_regression_attestation` remains false; PR #37 remains draft.

Current local validation: **2,127 passed, zero failures/skips**, 58 upstream deprecation
warnings, 261.76 seconds. The final gateway-only rerun, including delayed Linux-select timing,
passed all 36 tests in 10.45 seconds. Strict mypy: 227 files; Ruff lint/format: 329 files.
OpenAPI, six schema/enumeration bindings, frozen logical fixture and 74-operation client drift
checks pass. Current-head GitHub CI remains required; the preceding head's Python job failed
as documented above, while Node, documentation and security jobs passed.

### Canonical candidate source/build inputs (continuation)

Migration 0031 records the prepared candidate's actual content-tree digest, exact changed paths
and canonical source snapshot in the same transaction as its build claim, before Docker starts.
Failure after source insertion rolls back the claim, verification, source record and patch
transition together. The snapshot's commit remains the base commit as lineage; approved in-memory
changes are explicitly dirty, not a fictitious clean commit. Dirty paths include mode-only changes,
additions and deletions, while no-op patch entries do not count as changes. The existing v1 content
tree digest does not include file mode; the independently captured candidate archive digest does.

After retention and fresh bounded byte read-back, the coordinator publishes a canonical
`build_artifact` linked to that captured candidate snapshot, not the baseline snapshot. The observed
artifact flag describes those actual output bytes, not a claim that a browser or deployment served
them. Source/build IDs are now available for subsequent candidate sealing; no environment or run is
created here. Historical builds get no invented source capture and cannot use this publication path.

Publication rechecks current patch/source/approval/retention authority and exact captured source
and output identities, including on idempotent repeats. Repeating metadata publication returns the
same IDs without rebuilding. A failed metadata publication after BUILT can be retried through the
metadata-only function; it must not rerun the candidate build. Captured source and first published
output binding are immutable. Missing or changed source/build rows, wrong workspace and unavailable
retained bytes fail closed. Real encrypted backup/isolated-restore tests preserve both unpublished
and published materializations, without converting them to reader evidence.

Remaining is still the actual endpoint/environment/run/lease binding and canonical reader/observer
path, plus E0 controller and UNKNOWN reconciliation. This input-materialization slice does not
enable `_regression_attestation` or satisfy Module 15's actual-VoiceOver acceptance gate.

Validation: **2,134 passed, zero failures/skips**, 58 upstream deprecation warnings, 266.02 seconds.
This includes the actual source-capture rollback fault and retained/quarantined/deleted byte
backup/restore with materialization identity checks. Earlier focused integration run: 72 passed;
snapshot suite: 64 passed. Strict mypy: 228 files; Ruff lint/format: 330 files; all four contract
drift checks pass. The preceding endpoint-lifecycle head
`6149426c098322a238edbc472126dff66b2bd286` passed all GitHub CI jobs in run 34724314684,
including the Linux expiry regressions and real backup/restore. This materialization head requires
its own CI and remains an incomplete draft, not a verified repair.

### Exact live candidate run and first lease (continuation)

Migration 0032 and the trusted `CandidateSession.prepare_run(environment_id)` callback now bind
the current regression/endpoint to a canonical run and seal, using the actual materialized
candidate source/build records. The environment must be separately authorized by a current named
workspace member, match only the exact live loopback origin, and expire no later than that
endpoint. Patch approval does not create environment or RUN_EFFECTS authority. Baseline environment
settings other than origin/expiry, every sealed evaluator/model/profile/journey/policy input, and
fixture logical values/oracle contract remain frozen. The baseline must be completed FAIL with
closed recorded producers; no synthetic baseline is manufactured by the implementation.

New fixture instances capture an immutable creation-time contract digest. Existing fixture rows
retain NULL and cannot be backfilled into historical proof. Candidate preparation refuses those
rows and reuses neither the baseline nonce nor another run's nonce: its fresh nonce must be the
actual gateway fixture path. Preparation commits the run, fixture, seal and exact verification
binding atomically. Conclusion cannot substitute another run or widen the derived environment-only
comparison allowance. No run is executed or implicitly authorized merely by being prepared.

The first admitted desktop lease is bound once, with exact run/profile/epoch and a deadline within
the endpoint lifetime. Admission, dispatch revalidation and live gateway requests recheck current
candidate authority. Changed profiles, revocation, cancellation, lease release/expiry or a deadline
extended beyond the endpoint fail closed. The trusted setup GET is available before lease admission;
after binding a lease, even GET requires that original live lease. Bound-session POST deliberately
refuses until the canonical controller supplies independent RUN_EFFECTS transport authorization.
The older unsealed preview callback remains a separate backend probe, not a reader run.

Both callback return and callback exception require resolution of any bound reader lease. An
unresolved lease or unavailable cleanup state leaves the regression UNKNOWN, even after the owned
containers/listener are removed. Expiry is not a stop acknowledgement. The controller does not mark
a candidate reader run completed, invent observer events, or enable `_regression_attestation`.

Real-container integration tests use explicitly synthetic baseline/desktop metadata to exercise
the control-plane binding, live GET, released-lease HTTP502 refusal, immutable identity guards,
independent authority boundaries and unresolved-reader return/error paths. This is not actual
VoiceOver, browser focus/navigation, canonical observer closure, or a verified repair. A disposable
in-memory mutation restoring the pre-fix normal-return-only cleanup check makes the raised-active
regression fail; the fixed path passes and retains UNKNOWN. Tests with terminal baseline seeds
drop their exact generated database rather than bypass terminal immutability.

Still required: canonical authorized controller/reader/observer execution and finalization,
original-daemon UNKNOWN reconciliation, actual matched failure-to-repair proof, and current-head CI.
PR #37 stays draft. Preceding materialization commit a80b112 passed all CI in run 34724965751.

Final local validation for this continuation: **2,138 passed, zero failures/skips**, 58 upstream
deprecation warnings, 285.38 seconds, using a fresh disposable PostgreSQL database and the explicit
pinned local Docker toolchains. Focused live-binding/forward-upgrade run: four passed in 20.37
seconds. Strict mypy: 229 files; Ruff lint/format: 331 files. OpenAPI, contract bindings, logical
fixture identity, generated clients and internal documentation links pass. This does not claim
current-head GitHub CI or actual-reader acceptance.

### Complete canonical execution identity (continuation)

The earlier `sealed_manifest.manifest_digest` was an input fingerprint: it excluded run identity,
authorization identity, journey version ID, expiry, budgets and permitted effects. It therefore
did not satisfy the complete `RunManifest` in CONTRACTS section 4. Migration 0033 adds immutable
`canonical_manifest` storage without inventing any of those missing historical values; old seals
remain NULL and cannot be backfilled into consent or execution evidence.

`projects.seal_run(..., execution=ExecutionInputs(...))` now constructs and validates the complete
existing JSON Schema before hashing/storing it. It requires reserved exact run/authorization IDs,
the matching frozen journey version/project and all journey/assertion/fixture/policy digests,
this project's exact observed source/build pairing, positive schema-valid budgets, permitted
effects within the environment and bounded execution expiry. The persistence package reuses the
existing local contract validator; no third-party dependency version or schema definition changed.

Candidate preparation now requires a full baseline manifest and creates a fresh canonical
candidate run/authorization identity, retaining the baseline journey, budgets and effects while
binding the candidate's source/build/environment. Current manifest digest/expiry and both the
endpoint and execution deadlines are checked at live admission. A reserved authorization ID is
stored on the run, but **no approval row is issued**. Bound-session POST and verification remain
closed until the separate authorization/controller path actually exists.

The database refuses replacing a canonical run's digest/authorization/project or reusing its
manifest for another run. Canonical sealing also refuses retroactively resealing an existing run;
the SQL insertion guard rejects replacing its identity even when a writer bypasses the Python
helper. HTTP run admission uses the reserved IDs, rejects a different supplied
authorization or repeat admission with a structured 400, and serializes competing requests before
charging quota. Existing legacy input-seal consumers retain their metadata-only behavior; the
public seal-creation route still produces those legacy input fingerprints, not execution consent.

Tests cover complete schema payloads, per-axis digest changes, invalid IDs/budgets/effects/expiry,
wrong journey or build/source, absence of implied approval, and a real pre-0033 historical seal
upgrade. Actual-container candidate tests continue to use synthetic baseline/desktop metadata,
not an actual reader. A scratch concurrent real-HTTP probe admits exactly one of two requests
(202/400). Canonical manual RUN_EFFECTS approval, full controller/reader/observer finalization,
UNKNOWN reconciliation and actual matched repair acceptance remain required. Draft PR #37 is not
ready for merge. Previous binding commit b1367c9 passed all CI in run 34726565787.

Canonical/API runtime validation: **2,156 passed, zero failures/skips**, 58 upstream warnings,
291.12 seconds. The subsequent packaging follow-up passed the updated full unit suite:
**1,041 passed** in 18.61 seconds. Strict mypy: 232 files; Ruff: 334 files. Four contract/client
drift checks and unchanged specification inputs pass. These are distinct validation checkpoints,
not a claim that the earlier full suite included the later packaging tests.

Real package builds also exposed two pre-existing distribution failures hidden by editable
imports: persistence forced its already-included SQL files into wheels a second time, and the
contracts sdist omitted the sibling schema directory needed when rebuilding a wheel. The redundant
SQL inclusion is removed. A small [Hatch custom build hook](https://hatch.pypa.io/1.13/plugins/build-hook/custom/)
now carries schemas into sdists and direct wheels without duplicating files in sdist-built wheels.
It uses the existing build backend, not a new service or SDK, and reads only package schema files.

The new mandatory CI packaging check builds both paths and compares all **six schema** and
**33 migration** files byte-for-byte with their sources, checks duplicate entries and the local
contract dependency metadata. Actual built wheels were installed into a temporary target; module
origins and packaged resources were checked there. Other runtime dependencies remained in the
existing test environment, so this is not a hermetic deployment or a published release. Current-head
GitHub CI, independent execution approval and actual-reader proof remain required.

### Public canonical seal creation and review (continuation)

`POST /projects/{projectId}/seals` now accepts an optional `execution` object containing exactly
`journeyVersionId`, `expiresAt`, `actionBudget`, `wallTimeBudgetSeconds`, and `permittedEffects`.
The existing source/build/environment and input-digest fields remain required. The server reserves
fresh run/authorization UUIDs and returns the complete schema-validated `canonicalManifest` plus
its digest and `manifestKind: CANONICAL_EXECUTION`. It creates neither a run nor an approval.
Malformed nested fields, non-integer/boolean budgets, duplicate effects, effects outside the
environment, mismatched frozen journey inputs and invalid/expired/excessive expiry are refused.

`GET /projects/{projectId}/seals/{sealedManifestId}` returns the exact immutable payload for human
review, with the manifest digest as ETag. It is scoped to the exact project and workspace and uses
EVIDENCE_READ, whereas creation requires PROJECT_CONFIGURE plus CSRF. It intentionally permits
historical inspection after expiry or environment revocation, without claiming current execution
authority. Lists distinguish canonical execution identities from legacy input fingerprints.
Omitting `execution` still produces a legacy fingerprint with NULL canonical payload; history is
not backfilled. Cached pre-upgrade responses retain their original shape on same-project replay.

Local review found a pre-existing project-path replay defect: a cached seal under the shared route
template could be returned through another project's URL. The fix checks the cached seal's actual
project before responding, while keeping the old key namespace so legitimate pre-upgrade retries
do not allocate duplicates. Both canonical and old-shape legacy cache regressions fail under a
scratch-only mutation removing that check (wrong-project HTTP201), and pass with it (HTTP400).

Canonical run admission now rechecks the persisted full schema/digest/reserved IDs, execution expiry
and current environment usability before charging quota. Requesting still does not approve or
start execution. Full manual RUN_EFFECTS issuance/revocation and dispatch, actual reader/controller/
observer finalization, UNKNOWN reconciliation and matched repair acceptance remain pending.
No new external integration, dependency version or database migration is introduced by this slice.
The live contract and both generated operation tables now contain 75 operations.

Validation: full Python checkpoint **2,188 passed**, zero failures/skips, 58 upstream warnings,
294.92 seconds on fresh PostgreSQL and pinned local Docker toolchains. That run was collected
before the final missing-cache-result guard, which refuses incomplete stored operations with409
without re-execution. Final focused HTTP plus all unit tests: **1,095 passed** (54 HTTP +1,041 unit),
one upstream warning,28.98 seconds. These are separate checkpoints. Strict mypy232, Ruff334,
four drift checks, documentation links and recursive TypeScript typecheck/build/test pass.
The preceding commit0df922e passed GitHub CI34728157381; the new commit requires its own CI.

### Exact manual execution consent (continuation)

Manual consent is now exposed at `/projects/{projectId}/seals/{sealedManifestId}/approval`:
POST issues the reserved authorization ID, GET inspects the decision, and POST to the appended
`/revocation` path irreversibly withdraws it. Mutation requires RUN_APPROVE (owner/maintainer), CSRF,
the exact reviewed `manifestDigest`, and `If-Match` containing the seal's `revision` from GET.
Issuance additionally requires explicit future `expiresAt` no later than the canonical execution
expiry. The digest ETag is not the integer revision header. Issuance and its attributed audit row
commit together; it does not start or even create a run. Same-key responses are historical operation
receipts, not cached dispatch permission; GET reads current revocation state and dispatch rechecks it.

Migration0034 adds the seal's fixed initial `authorization_revision=0`. It is an immutable target:
the full canonical payload already binds the exact run, source/build/environment, effects, budgets
and expiry. A changed scope requires a new seal/authorization, whereas acquiring a desktop merely
advances the run's operational revision. No missing historical consent is backfilled. SQL makes
all exact approval identities immutable, permits only one-way revocation, and prevents deleting a
canonical approval for a retained seal and reissuing its reserved ID. Existing workspace erasure
and legacy decision preservation remain distinct from reauthorization.

`runners.assert_manual_dispatch_authorized` loads the persisted manual approval, exact seal, current
approver permission and environment afresh; it shares the actual runner/lease/profile/preflight
checks with the existing R1 child/grant gate, without fabricating a parent grant. Shared checks now
also require this lease's exact run, live deadline/current epoch, uncancelled unquarantined LEASED
run and matching preflight identities. Passing this gate is not deployment or actual-reader proof,
nor permission to skip per-action effects/budgets, journaling or physical stop acknowledgement.
The canonical controller still must invoke it at committed dispatch; that controller and the
bound-session POST transport remain unfinished/closed.

Restore reconciliation irreversibly revokes restored canonical RUN_EFFECTS approvals and reports
their count in its receipt/audit. A real `pg_dump`/`psql` test snapshots live consent, revokes it in
the original database, restores the earlier snapshot into an exact owned disposable database, and
proves reconciliation invalidates the resurrected decision. Reapproval requires a newly reviewed
seal, not resetting the old revocation. A scratch-only mutation ignoring persisted revocation makes
both the direct revocation and actual restore regressions fail; unmodified code passes.

Focused API/dispatch/forward-upgrade validation:136 passed, one upstream warning,26.84 seconds.
Concurrent real HTTP issuance admits one of two requests(201/409), with one approval and audit row.
Synthetic desktop/preflight metadata prove the control-plane gates, not VoiceOver, focus, observer
closure or a repaired outcome. Current-head CI and actual controller/reader/observer
acceptance remain required. The preceding commit0580238 passed GitHub CI34729010746.

Final local regression for this consent continuation: **2,223 passed**, zero failures/skips,
58 upstream warnings,298.94 seconds, using fresh disposable PostgreSQL and the pinned local Docker
toolchains. Strict mypy233, Ruff335, four contract/client/fixture drift checks, documentation links
and recursive TypeScript typecheck/build/test pass. Actual built packages retain all six schemas
and34 migrations byte-for-byte. The scratch workspace-erasure probe passes without retaining
an approval or bypassing the anti-reissue trigger. No actual reader or committed controller dispatch
is inferred from these tests; draft PR37 remains incomplete and unmerged.
