# Private native consent provisioning

This change connects an existing operator consent decision to the native bootstrap. It adds no
consent issuance, OS permission changes, reader startup, deployment or production profile proof.
It builds on PR #38; the separate operator UI is PR #39.

## Operator export

After an infrastructure operator explicitly records consent, the authenticated human CLI can read
the run, freshly reviewed runner scope and stored consent and export their matching reference:

```sh
accessforge --base-url https://YOUR-ACCESSFORGE-HOST runner export-startup-consent \
  --workspace WORKSPACE_UUID --run RUN_UUID --runner RUNNER_UUID \
  --output /ABSOLUTE/PRIVATE/DIRECTORY/reader-consent.json
```

Replace placeholders with the operator's real deployment and exact IDs. The existing CLI session
mechanism is unchanged. No credentials are written into the exported reference. The parent
directory must already exist, be canonical, owned by the operator and private (mode 0700). The
command creates a new file with mode 0600 and refuses to overwrite any existing path. No automatic
mkdir, chmod, cleanup or retry occurs. On incomplete export, inspect the exact output before
choosing a new path; a failed write may leave a partial file which the loader will reject.

Export refuses stale, cancelled, quarantined, mismatched, expired, revoked or already session-bound
consent. The three reads are a snapshot, not atomic execution authority; revocation after export
remains effective through the existing machine-side live checks.

## Trusted native embedding

The host integration can call `createProvisionedExecutionBootstrap` from
`apps/desktop-runner/src/execution-bootstrap.ts`, supplying the existing trusted execution options
plus `readerStartupConsentPath` instead of the inline `readerStartupConsent` object. This is an
embedding API, not a new autonomous daemon or a navigator-controlled file argument.

The loader reads only a bounded, owned, private, regular single-link file. It rejects symlinks,
extra fields, identity mismatch, stale expiry and effects differing from pinned Guidepup behavior.
It returns a frozen five-field consent scope, never a bearer credential. The bootstrap still
requires independent host authorization, production profile eligibility, physical preflight and
authenticated server consent rechecks before and after initialization. The reference cannot
enable currently unproven production matrices or bypass the runner CLI's existing refusal.

This protects the cooperative operator boundary, not malicious same-user/root processes. Keep
the file in the private directory created for provisioning; transferring ownership to a separate
runner account requires deliberate operator provisioning rather than making it world-readable.

## Validation and remaining work

Changed-file Ruff/mypy and desktop TypeScript build passed. Focused inert client and private-file
regressions are authored for CI only; no local test suite, actual export, reader or browser was run.
CI must pass at the exact PR head before merge. Real trusted-host provisioning, physical reader
proof and production deployment remain pending.
