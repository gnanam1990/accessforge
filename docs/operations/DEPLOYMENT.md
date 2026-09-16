# Deployment

## Current direction: local Codex; non-AWS hosting remains unproven

The owner retired AWS/Bedrock on 2026-09-16. This guide is the active operator path;
the original AWS proposal is historical, not a task to resume. Service startup, model
connectivity and actual-reader acceptance are separate evidence classes.

| Target | Status | What that means |
|---|---|---|
| Local control plane | **IMPLEMENTED; fresh rehearsal required** | Historical service checks are in [handoff 27](../handoffs/27.md); they do not prove the current complete reader/repair journey. Use the commands below only against dedicated local resources. |
| Hosted (non-AWS) | **NOT DEPLOYED** | Destination, production identity, backup/restore, capacity and operator approval remain required. Codex is the model integration, not the deployment host. |
| AWS / Bedrock | **RETIRED** | [`infra/aws/`](../../infra/aws/README.md) preserves the old proposal. Do not provision those resources or enable Bedrock for the current product. |
| Actual-AT execution (VoiceOver / NVDA) | **UNPROVEN** | Qualify the exact dedicated desktop, permissions and reader/browser profile. A Mac being available does not qualify it; real Windows/NVDA acceptance is also outstanding. |
| Model integration (Codex ChatGPT OAuth) | **IMPLEMENTED; execution-host acceptance required** | Use the pinned CLI and the operator's existing login store. Exact model consent is separate from reader startup and deployment approval. See [Codex migration](../handoffs/codex-migration.md). |

---

## 1. Clean install on a fresh machine

### 1.1 Before anything

```bash
uv run python scripts/doctor.py
```

Runs every check and prints every result rather than stopping at the first failure, because a
bootstrap that stops at the first problem turns a ten-minute setup into an afternoon. It never
installs anything and never prints a credential — a credential is reported as present or absent, and
a masked prefix is still an oracle.

It distinguishes three conditions with three different remedies:

- `MISSING` / `WRONG VERSION` — install or change a tool
- `OUT OF DATE` / `MISCONFIGURED` / `UNREACHABLE` — run or fix something
- `BLOCKED` — a person's action: a permissions grant, a machine, an entitlement. No package manager
  gets you out of these.

Exit 0 means the checks covered by the doctor passed. It does not establish a working complete
product journey. `BLOCKED` capabilities do not fail that diagnostic; inspect them separately
before model or actual-reader execution.

### 1.2 Toolchain

Versions come from [`infra/version-matrix.toml`](../../infra/version-matrix.toml), which
`tests/unit/test_version_matrix.py` checks against `pyproject.toml`, `package.json` and the CI
workflow — so a dependency bump that forgets the matrix fails rather than leaving a stale record of
what was tested.

| | |
|---|---|
| Python | ≥ 3.13, < 3.14 |
| uv | 0.11.28 exactly |
| Node | ≥ 22, < 23 |
| pnpm | 11.10.0 exactly |
| PostgreSQL | ≥ 17 (server and `pg_dump`) |

### 1.3 Install and configure

```bash
uv sync --frozen                          # fails if uv.lock disagrees with pyproject.toml
pnpm install --frozen-lockfile --ignore-scripts
cp .env.example .env                      # then fill it in
```

Startup fails closed on placeholder values — substring matching, not equality, so
`REPLACE_ME_WITH_A_RANDOM_VALUE` is refused.

`ACCESSFORGE_IDENTITY_PROVIDER=local-development` accepts an email with **no secret**. That is an
authentication bypass by construction, and startup refuses it unless `ACCESSFORGE_ENVIRONMENT=local`
— setting the variable is not enough.

### 1.4 Services and schema

```bash
# PostgreSQL 17 and an S3-compatible store must be running. There is no filesystem fallback.
uv run python scripts/migrate.py
uv run python scripts/doctor.py           # expect: "This machine can run the local product path."
```

The application role must be `NOSUPERUSER NOBYPASSRLS`. Tenant isolation **is** row-level security;
a bypassing role has none, and the doctor reports it as `MISCONFIGURED`.

### 1.5 Run it

```bash
uv run python -m accessforge_api          # control plane
pnpm --filter @accessforge/web dev        # web UI
```

### 1.6 Recover abandoned manual handoffs

Run a separate supervised process for the explicitly configured workspace(s):

```bash
uv run python -m accessforge_orchestrator.maintenance.handoff_worker \
  --workspace-id <workspace-uuid> --interval-seconds 5
```

It reads `ACCESSFORGE_DATABASE_URL` and uses workspace-scoped transactions, not an elevated
cross-tenant discovery connection. Repeat `--workspace-id` for additional authorized workspaces.
`--once` performs one bounded pass and exits nonzero if recovery was unavailable or deferred.
No migration or infrastructure provisioning happens at startup.

The worker interrupts/quarantines exact committed manual attempts when an unaccepted dispatch
ticket expires, the ticket is revoked, or its lease expires/is released without acknowledged STOP.
It never retries dispatch, kills a reader, resets a desktop, or reports an accessibility verdict.
A consumed bootstrap ticket expiring alone does not end a healthy execution lease. Fresh attempts,
newer runner epochs, terminal runs and acknowledged stops are left alone. Each recovered attempt
commits separately and publishes the existing `run.interrupted` event; contention is deferred.

Keep this process supervised for periodic recovery. SIGINT/SIGTERM stops after the current bounded
pass. This covers abandoned manual handoffs, not every build/action/observer UNKNOWN state, and
does not enable actual reader execution. No worker has been deployed automatically by these docs.

---

## 2. Deploy order — fixed, and one-directional

### Downloadable release candidate

The trusted release workflow packages a source-and-web ZIP after its required CI completes.
The workflow artifact is named `accessforge-release-<commit>` and contains the ZIP plus its
SHA-256 checksum. It is retained for 30 days; it is not a GitHub Release publication or deployment.
The ZIP includes committed source with lockfiles, freshly built web assets, a per-file digest
manifest and an installation/limitations note. Untracked source files (including local `.env`)
are excluded; source/web symlinks and an existing output path are refused.

To package locally, first commit reviewed source changes and build the web from that checkout:

```bash
pnpm --filter @accessforge/web build
python scripts/package_release.py --output accessforge-release.zip
```

The script prints the outer SHA-256 checksum. It requires a clean tracked checkout and never
installs dependencies, applies migrations or contacts a model. The manifest inventories bytes;
it is not a signature, CI attestation or proof that arbitrary caller-supplied assets match the
source. The trusted workflow builds and packages together. Dependencies and desktop/model runtimes
are not bundled: installation remains the locked source workflow above. Static web hosting must
provide SPA fallback and same-origin `/v1` API routing. Actual reader, model, repair/rerun/review,
fresh-install and hosted acceptance remain separate requirements.

### Service upgrade order

1. **Take a backup.** A migration is the change a restore exists for.
2. `scripts/migrate.py` — a single, separate, deliberate step.
3. Start the new binaries.

**Startup does not migrate.** Two replicas booting together would migrate concurrently, and a binary
that migrated on boot would migrate *forward* again during a rollback, turning a rollback into a
second upgrade. `/health/ready` reports the result of step 2 rather than performing it: a process
that comes up against an un-migrated database answers 503 naming the reason and takes no traffic.

Readiness covers three dependencies — the database, the schema, and the evidence store — and
deliberately reports a fourth as unknown. `desktopRunner` sits **outside** `dependencies` because
nothing polled from a control plane can establish that a machine somewhere is attached and driving a
real screen reader. As a passing check it would claim a desktop; as a failing one it would make a
healthy control plane look broken.

Shutdown drains: the server stops accepting connections and waits for in-flight requests. Every
mutation is a single transaction, so an interrupted request rolls back and the caller's
`Idempotency-Key` makes the retry exact. There is nothing to clean up on the way out.

---

## 3. CI: trusted and untrusted are separate workflows

| | `ci.yml` | `release.yml` |
|---|---|---|
| Trust | **Untrusted.** Runs pull-request code, including forks. | **Trusted.** Cannot be triggered by a fork. |
| Triggers | `pull_request`, `push` to main, `merge_group`, `workflow_call` | `workflow_dispatch`, `push` of a `v*` tag |
| Secrets | **None referenced.** Asserted structurally by a step that parses the file rather than grepping it. | None either — a release candidate is artifacts and a record, and publishing is a person's decision. |
| Lifecycle scripts | Disabled (`--ignore-scripts`). A dependency's `postinstall` is arbitrary code from a transitive package, running before any check has. | Same, via `workflow_call`. |
| `pull_request_target` | Not used anywhere. Asserted by parsing every workflow's trigger keys. | |
| Can it deploy? | No. | **No.** |

`release.yml` calls `ci.yml` rather than copying its steps — two copies of a pipeline diverge, and
the copy that diverges is always the one nobody reads. It additionally refuses a tag that is not an
ancestor of `main`, so "we released the tag" and "we released reviewed code" are the same statement.

A dedicated `platform-only` job prints, in the run log, everything the pipeline does **not**
exercise, and fails if the capability matrix stops agreeing with it. It exits 0 on purpose: these are
absences rather than failures, and a permanently red X trains everyone to ignore a red X.

---

## 4. Non-AWS release prerequisites — not a deployment certificate

No deployment destination has been selected or provisioned by these instructions. Before an
authorized release, record the exact destination and prove:

1. Production identity/session configuration, HTTPS and restricted network access. Never expose
   the local-development sign-in bypass to remote users.
2. PostgreSQL 17 with a non-superuser, non-RLS-bypassing application role; separate migration
   authority; a verified backup and restore into a separate target before applying live changes.
3. Private S3-compatible evidence storage and protected backup storage, with explicit retention,
   encryption/key recovery and restore evidence. The S3 protocol and storage libraries do not
   require choosing AWS; local MinIO tests are not production storage qualification.
4. Supervised API and required worker processes, health/readiness, bounded shutdown, capacity and
   original-operation recovery. A successful release workflow creates artifacts, not a deployment.
5. The execution host's pinned Codex CLI and operator-owned ChatGPT OAuth login, with exact
   per-run consent. Do not extract tokens into app config or substitute AWS/API-key credentials.
   Result-admission token holds are not a provider spending cap or a CLI-internal retry cap.
6. Dedicated qualified macOS/VoiceOver and Windows/NVDA hosts for their declared release scopes.
   Reader startup and OS permission changes require their own approval; containers do not supply
   an actual interactive assistive-technology session.
7. A real baseline, constrained repair, independent rerun, human review and verified offline
   export, followed by a fresh operator rehearsal. Synthetic CI cannot satisfy these items.

Use the [execution ledger](../delivery/EXECUTION-LEDGER.md) for the current continuation queue.
The [retired AWS proposal](../../infra/aws/README.md) is retained solely for provenance. No
resource creation, live migration, model invocation or permission grant occurs from this guide.
