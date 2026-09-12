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

## Current proof

- 62 focused source-preparation and CI-coverage tests pass.
- Strict mypy passes across all 200 source files.
- Ruff lint and formatting pass.
- Full unit suite: 912 passed.
- Full Python runtime suite: `uv run pytest -q --tb=short` — 1,923 passed, zero failures/skips,
  58 upstream deprecation warnings, including real PostgreSQL/MinIO and restore tests.
- Live OpenAPI and generated binding drift checks pass.

These tests include actual in-memory tar parsing and deterministic candidate construction, but
**no build script or sandbox has run**. They are not containment or reader proof.

## Required next work

1. Bind intake to the persisted immutable source commit/artifact, not an author-supplied archive.
   Module 05's existing content-tree digest omits executable bits. The new canonical archive digest
   includes them, but the coordinator must bind that exact archive to trusted source provenance
   before dispatch; equal v1 tree digests alone must not authorize unexplained mode drift.
2. Claim build work atomically in PostgreSQL, reload current PATCH_APPLY authority immediately
   before sandbox dispatch, persist the exact input/configuration identities and fence stale workers.
   Pure preparation checks cannot establish that an approval remains unrevoked after it was fetched.
   Existing `open_verification` transitions the patch from APPROVED to BUILDING and increments its
   revision. The coordinator must bind the original approved revision and the claimed building
   revision explicitly; it cannot reuse an APPROVED-only dispatch check after that transition or
   compare an approval's expected revision to itself.
3. Implement the E0 disposable execution boundary with a pinned trusted toolchain, non-root identity,
   no host/credential/socket mounts, network denial and bounded CPU, memory, scratch, logs and time.
4. Run protected functional regressions outside source-writable paths; independently collect and hash
   candidate output. Repository stdout and self-reported identities are never authority.
5. Exercise real containment canaries, metadata/egress refusal, resource exhaustion, cancellation,
   crash, durable recovery and exact task-owned cleanup. No blind retry after ambiguous execution.
6. Connect the proven build to module 15's fresh matched actual-reader run. VERIFIED remains
   unavailable without complete trusted evidence and protected regression results.

No candidate was published, no hosted arbitrary-source support is enabled, no model call or paid
service was used, and the user checkout was not used as a candidate workspace.
