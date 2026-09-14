# Baseline artifact retention

`build_and_retain_baseline` composes the approved source preparation result, owned Docker build
dispatch, immutable capture receipt and separate archive retention. It does not start an app,
fixture endpoint, browser or assistive technology.

The original sealed artifact digest must equal the captured archive digest. A database intent is
committed before create-only upload. Exact process/source/image/daemon identities and original
run approval are checked before upload; authority, location, expiry and retirement are checked
again before promotion after bounded byte read-back. A failed upload stays quarantined, and a
repeated call cannot launch another build or replace an existing archive intent.

Every retained read checks the archive namespace, original capture digest, storage location,
current retention policy, retirement intent and actual bounded bytes. Reading bytes is not an
execution grant: runtime callers still need fresh execution authority and protected endpoint
admission. CAPTURED and RETAINED do not imply successful functional or reader assertions.

Expiry commits retirement intent before storage I/O. The existing create-only storage protocol
replaces payload with a permanent empty tombstone, blocking late uploads. This requires the same
never-versioned, non-replicated, non-expiring bucket constraints as candidate archives. Completion
only describes the active store, not prior backups or external copies. Retry finishes the same
retirement. Original capture receipts remain immutable.

Backups already enumerate the entire bucket. Restore treats baseline archive keys as create-only,
including empty tombstones, and appends verified target locations without rewriting original
provenance. Restored queued run approvals remain subject to normal reconciliation revocation.

Focused validation uses real PostgreSQL/HTTP approval and lifecycle boundaries, synthetic process
receipts and in-memory object storage. It covers retention, upload failure, substituted bytes,
approval revocation during upload, policy expiry, repeat retirement, and restore overwrite refusal.
This does not establish actual baseline Docker/S3 execution, a complete baseline archive restore
drill, or actual VoiceOver acceptance. Those integration checks and the protected baseline runtime
are still required before release acceptance.
