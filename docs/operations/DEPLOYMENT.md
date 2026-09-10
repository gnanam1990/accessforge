# Deployment

## Status: local is GO. Hosted is **PREPARED**.

| Target | Status | What that means |
|---|---|---|
| Local (a developer's machine) | **GO** | The complete declared local product path runs, from a clean checkout. Commands below; results in [handoff 27](../handoffs/27.md). |
| Hosted (AWS) | **PREPARED** | A written proposal exists in [`infra/aws/`](../../infra/aws/README.md). Nothing has been created. No AWS credential is configured in this environment and no workflow in this repository can apply it. |
| Actual-AT execution (VoiceOver / NVDA) | **BLOCKED** | Needs a macOS host with Accessibility and Automation grants, and a Windows host with NVDA. Neither is available here. |
| Model invocation (Bedrock) | **BLOCKED** | Needs credentials **and**, separately, approval to incur charges. Credentials alone are not approval. |

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

Exit 0 means the local product path will run. `BLOCKED` capabilities do not fail it: the local path
does not exercise them, and failing on them would make a correct machine look broken.

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

---

## 2. Deploy order — fixed, and one-directional

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

## 4. Hosted deployment — the exact external changes

None of these has been made. Each requires explicit approval from whoever owns the account.

| # | Change | Billable | Reversible |
|---|---|---|---|
| 1 | Create a VPC with private subnets and an S3 gateway endpoint | Yes (NAT) | Yes |
| 2 | Create an RDS PostgreSQL 17 instance, Multi-AZ, encrypted with a customer-managed KMS key | Yes | Yes, with data loss on delete |
| 3 | Create an S3 evidence bucket: versioned, public access blocked, SSE-KMS, TLS-only | Yes | Yes |
| 4 | Create a separate S3 backup bucket, ideally in another account | Yes | Yes |
| 5 | Create two KMS keys (data, backups) | Yes | **Scheduled deletion only.** Deleting the backup key makes every archive sealed with it permanently unreadable. |
| 6 | Create Secrets Manager entries for the database URL, object-store credentials and backup key | Yes | Yes |
| 7 | Create an SQS queue and dead-letter queue | Yes | Yes |
| 8 | Create four IAM roles from [`infra/aws/iam/`](../../infra/aws/iam/) | No | Yes |
| 9 | Create an ECS cluster, an API service (2 tasks) and a run-once migrator task | Yes | Yes |
| 10 | Create AWS Budgets alarms on model spend and total account spend | No | Yes |
| 11 | **Enable Bedrock model access** | **Yes, per invocation** | Yes |
| 12 | Provision a macOS host for VoiceOver, outside this configuration | Yes | Yes |

Changes 11 and 12 are separate approvals, not part of a deployment approval. Enabling Bedrock access
is not approval to invoke a model, and provisioning a Mac is not evidence that VoiceOver can be
driven under automation on it — that remains unverified here.

**Not automated on purpose.** Nothing in this repository should be one command away from creating
billable resources in somebody's account.
