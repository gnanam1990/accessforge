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

## Required next work

1. Integrate the persisted-source broker with the exact patch/baseline manifest and durable build
   claim. Persist its mode-inclusive archive identity before dispatch; equal v1 content-tree
   digests alone still must not authorize unexplained mode drift. Retained dirty-artifact intake
   remains separate required work if dirty E0 candidates are supported.
2. Claim build work atomically in PostgreSQL, reload current PATCH_APPLY authority immediately
   before sandbox dispatch, persist the exact input/configuration identities and fence stale workers.
   Pure preparation checks cannot establish that an approval remains unrevoked after it was fetched.
   Existing `open_verification` transitions the patch from APPROVED to BUILDING and increments its
   revision. The coordinator must bind the original approved revision and the claimed building
   revision explicitly; it cannot reuse an APPROVED-only dispatch check after that transition or
   compare an approval's expected revision to itself.
3. Provision the actual E0 reference-application toolchain and connect it to the new execution
   primitive. Docker daemon access is supervisor authority, never an author-selectable endpoint.
4. Run protected functional regressions outside source-writable paths; independently collect and hash
   candidate output. Repository stdout and self-reported identities are never authority.
5. Exercise real containment canaries, metadata/egress refusal, resource exhaustion, cancellation,
   crash, durable recovery and exact task-owned cleanup. No blind retry after ambiguous execution.
6. Connect the proven build to module 15's fresh matched actual-reader run. VERIFIED remains
   unavailable without complete trusted evidence and protected regression results.

No candidate was published, no hosted arbitrary-source support is enabled, no model call or paid
service was used, and the user checkout was not used as a candidate workspace.
