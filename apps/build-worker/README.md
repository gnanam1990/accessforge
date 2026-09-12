# `apps/build-worker`

**Module 14 foundation implemented; end-to-end repair verification is incomplete.**

Isolated source, patch and build execution.

The worker binds persisted, approved patches to immutable Git source; performs one-time fenced
dispatch in a non-root, no-network Docker sandbox; captures bounded output; and retains verified
artifact bytes with tenant, process, daemon and storage provenance. Ambiguous execution remains
UNKNOWN, never automatically retried. A successful build is not VERIFIED.

## Owned reference-app toolchain

`toolchain/` is a trusted provisioning context containing only a digest-pinned Docker Official
Python base, hash-locked build/runtime requirements and a fixed PEP 517 frontend. Provisioning
downloads wheels from PyPI; it does not include or execute target source. The reference runtime
lock is a frozen export of `uv.lock`; its exact equality is tested. The separate Hatchling lock is
audited in CI. No dependency download is possible during actual candidate dispatch.

After provisioning the pinned base from `toolchain/Dockerfile`, run from the repository root:

```sh
uv run python scripts/provision_reference_toolchain.py --endpoint unix:///var/run/docker.sock
```

Use the explicit endpoint of your local daemon (on the tested Colima installation,
`unix:///Users/kratos/.colima/default/docker.sock`). The script prints an immutable local image ID,
not a mutable tag. Supply it as `ACCESSFORGE_REFERENCE_TOOLCHAIN` to the integration suite and
set `ACCESSFORGE_SANDBOX_ENDPOINT` to that same daemon. Actual dispatch uses
`accessforge_build_worker.toolchain.REFERENCE_BUILD_COMMAND`. This is trusted operator setup,
not a public endpoint or permission for hosted arbitrary-tenant execution.

To refresh the runtime export after an approved dependency update:

```sh
uv export --frozen --package accessforge-reference-app --no-dev --no-emit-workspace \
  --no-header --no-annotate -o apps/build-worker/toolchain/runtime-requirements.lock
```

The backend lock was generated with `uv pip compile --universal --python-version 3.13
--generate-hashes --no-annotate --no-header --default-index https://pypi.org/simple` from
`build-requirements.in`. Updating it changes trusted code executed inside the sandbox and requires
review, audit and reprovisioning. Installed wheels include platform-specific runtime packages;
CI provisions its own platform image and never reuses another daemon's local image ID.

Current proof builds the actual committed reference-app package after a deliberately build-only
approved edit, verifies every wheel source member, retains it in the real object store, and imports
the captured wheel inside a fresh contained process.

`ReferenceRegressions` additionally runs protected HTTP/database checks against captured wheels.
Provision its `POSTGRES_IMAGE` constant before execution. A task-specific network-none PostgreSQL
container, separate trusted HTTP driver, and candidate containers share only private loopback
networking; nothing is published to the host. The supervisor owns the schema, and the candidate
receives only fixture DML permissions, never administrator credentials. Direct SQL verifies
invalid-input refusal, exact successful submission, authorization/no-write boundaries, duplicate
handling and fresh-container restart durability. Late DB writers are fenced before the final read.
All containers are removed before a result is returned; ambiguous cleanup raises rather than
passing. Actual metadata/public-network and denied-DDL/admin canaries must pass.

This is currently a bounded local primitive, not a durable verification worker. No regression
attestation is written to a candidate run, and no API consumer may infer VERIFIED from its result.
Hard supervisor-crash reconciliation, a real accessibility repair and matched actual-reader proof
remain incomplete. Hostile multi-tenant execution remains unsupported.

See `docs/handoffs/14-candidate-build.md` for the current evidence and remaining work. The older
`14.md` describes the historical policy-only checkpoint, not the current worker implementation.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
