# Proposed AWS configuration — **PREPARED, NOT APPLIED**

Status, stated before anything else because it is the only thing in this directory that a reader
could get wrong:

| | |
|---|---|
| **Applied to any AWS account** | **No.** Nothing here has been created, ever. |
| Validated by `terraform validate` | No. No Terraform binary is present in this environment (`terraform: not found`, 2026-09-10). |
| Validated by an AWS API call | No. No AWS credentials are configured (`aws: not found`; capability matrix records Bedrock as BLOCKED). |
| What it is | A written proposal describing what would be created, sized against contracts proven locally. |

These files are **not** infrastructure that has been tested. They are the exact set of external
changes an operator would be asked to approve, written down so the approval is about something
specific rather than about a paragraph. Prompt 27 authorizes preparing this configuration and
requires separate, explicit approval before anything is provisioned.

Nothing in `.github/workflows/` can apply any of it. There is no `terraform apply` step, no cloud
credential, and no deploy job — see `.github/workflows/release.yml`, which says so and asserts it.

---

## What this proposal covers, and what it deliberately does not

**Covers:** the control plane, its database, its evidence storage, its job queue, and the identities
each of those needs.

**Does not cover, because it cannot:** the desktop runner. A runner drives a real screen reader on a
real interactive desktop session. AWS offers no service that provides one — not ECS, not Lambda, not
an EC2 instance running a container. An EC2 Mac instance provides a macOS host, and whether a
dedicated-host Mac can be made to run VoiceOver under automation with the necessary Accessibility
and Automation grants is **unverified here**, so it is proposed as a manually-provisioned host
outside this configuration rather than described as if it were solved.

**Does not cover:** Bedrock model invocation. The IAM policy below grants it, and granting it is not
the same as approving spend. See `docs/operations/RESOURCES-AND-COST-DRIVERS.md`.

---

## Proposed resources

| Resource | Proposed shape | Why this shape |
|---|---|---|
| RDS PostgreSQL | `db.t4g.medium`, PostgreSQL 17, Multi-AZ, 100 GiB gp3, storage encrypted with a customer-managed KMS key | Version 17 because row-level security with `FORCE`, and the `pg_dump` behaviour the restore drill depends on, are what the isolation model rests on. Multi-AZ because a control-plane outage stops every tenant. |
| RDS parameter group | `rds.force_ssl = 1`, `log_min_duration_statement = 1000` | Credentials must not cross a VPC in plaintext. |
| RDS backups | 35-day automated retention, plus the encrypted logical backup from `scripts/backup.py` | Snapshots restore a *database*; the logical backup restores a database **and** the evidence objects and key metadata that go with it. Neither replaces the other. |
| S3 evidence bucket | Versioning on, public access blocked, SSE-KMS with a customer-managed key, TLS-only bucket policy | Versioning because a promoted artifact's bytes must not be replaceable underneath a row that records its digest. |
| S3 lifecycle | **None configured.** | Retention is module 26's, enforced in the application against a workspace's policy. A lifecycle rule would delete evidence on a schedule the product does not know about, and the product would keep claiming the evidence exists. |
| SQS queue | Standard queue, 14-day retention, redrive to a dead-letter queue after 5 receives | The queue is a wake-up signal, never the source of truth: the transactional outbox is. A message lost here costs latency, not correctness — the job row survives in PostgreSQL and `scripts/restore.py` releases stale claims. |
| KMS | One customer-managed key for RDS + S3, one for backup archives | The backup key is separate because a backup is decrypted somewhere other than production, by a different person, during an incident. |
| Secrets Manager | Database URL, object-store credentials, backup archive key | Rotation of each is a runbook: `docs/operations/RUNBOOKS.md`. |
| ECS Fargate service (API) | 2 tasks minimum, 1 vCPU / 2 GiB | Two because a rolling deploy with one task is an outage. |
| ECS task (migrator) | Run-once task, executed **before** the service is updated | Startup does not migrate — see `scripts/migrate.py`. Two replicas booting together would migrate concurrently, and a binary that migrated on boot would migrate *forward* again during a rollback. |

## Files here

| File | Contents |
|---|---|
| `iam/control-plane-task.json` | The API task role. What a running control plane may do. |
| `iam/migrator-task.json` | The migrator task role. Database only; no object store, no model. |
| `iam/backup-operator.json` | The backup role. Bypasses nothing in AWS, but is the identity that holds the backup key. |
| `iam/ci-read-only.json` | What CI would be allowed, if it were ever given an identity. It currently has none. |

Every policy is written least-privilege and each statement carries a comment saying what breaks
without it, because a policy nobody can explain is a policy that gets widened during an incident and
never narrowed afterwards.

## To provision this

1. Read `docs/operations/DEPLOYMENT.md`, which lists the exact external changes.
2. Read `docs/operations/RESOURCES-AND-COST-DRIVERS.md`, which lists what drives cost. It contains
   no prices: prices change, and a number written here would be wrong and quoted anyway.
3. Obtain explicit approval for those specific changes from whoever owns the account.
4. Only then translate this into whatever provisioning tool that account uses.

Step 4 is deliberately not automated. Nothing in this repository should be one command away from
creating billable resources in somebody's account.
