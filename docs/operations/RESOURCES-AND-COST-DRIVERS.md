# Resource classes and cost drivers

**There are no prices in this document.** Not because they are unknown, but because a price written
here would be wrong within a quarter and quoted for a year. What a price cannot change is *what
drives the bill*, and that is what an operator needs in order to reason about a workspace that
suddenly costs ten times what it did last month.

Everything below is either a shape proven locally or a dependency that is explicitly BLOCKED. Where
a number is genuinely unknown, the row says so instead of estimating.

---

## 1. What actually drives cost

Four things, in the order they will surprise you.

### 1.1 Desktop-seconds — the dominant driver, and the one with no elastic supply

A run holds one physical desktop exclusively for its whole duration. Not a container: a machine with
a real interactive session and a real screen reader. This is the product's central constraint and it
is also its central cost:

* Desktops do not scale to zero and do not scale up in seconds. Capacity is a fleet you own.
* Concurrency is bounded by the number of desktops, not by anything software can widen.
  `workspace_entitlement.max_concurrent_runs` exists because of this.
* A run that hangs holds a desktop until its lease deadline. `max_wall_seconds_per_day` is the
  control; the lease deadline is the backstop.

**Measured locally:** nothing. VoiceOver and NVDA are BLOCKED (see `infra/version-matrix.toml`), so
no real run has ever been timed. Any figure for desktop-seconds per run would be invented, and this
is the number the whole cost model turns on.

### 1.2 Model tokens — spend that a bug can multiply

Metered as `MODEL_TOKENS` and bounded by `max_model_tokens_per_day`. What makes this different from
compute is that it is *per action*, and an agent that retries is an agent that spends again.

* Growth is superlinear in journey complexity: a longer transcript is a longer prompt on every
  subsequent call.
* An unbounded retry loop is the failure mode. The entitlement is a daily ceiling, not a per-run
  one, so a single pathological run can consume a workspace's whole day.

**Measured locally:** nothing. Bedrock access is BLOCKED. No model has been invoked by this system.

### 1.3 Evidence storage — small per object, unbounded over time

Every run produces artifacts, and evidence is never overwritten. Storage grows monotonically until
retention deletes something.

* The growth rate is runs × artifacts-per-run, not data volume from users.
* Versioning is on (an artifact's bytes must not be replaceable underneath a row that records its
  digest), so a deletion leaves a version until the version is expunged.
* **No S3 lifecycle rule is proposed.** Retention is enforced by the application against a
  workspace's recorded policy. A lifecycle rule would delete evidence on a schedule the product does
  not know about, and the product would go on claiming the evidence exists.

**Measured locally:** 1095 evidence objects totalling roughly 2.8 MB compressed inside an encrypted
backup, from the development database on 2026-09-10. That is a development corpus, not a workload.

### 1.4 PostgreSQL — steady, and the one you cannot undersize

The control plane's database is not large, but it is the authoritative record and every isolation
guarantee is enforced in it. Multi-AZ doubles the instance cost and removes a single-AZ failure as a
whole-product outage.

Growth is dominated by the sequencer's evidence records and the outbox, both of which are
append-only. The outbox is trimmed by retention; evidence is not.

---

## 2. Quotas and budget controls that already exist

These are implemented and tested, not proposed. All are per-workspace, revisioned, and configured by
an administrator — there is no checkout, no automatic top-up, and no currency column anywhere in the
schema.

| Control | Column | What it bounds |
|---|---|---|
| Runs per day | `max_runs_per_day` | Admission of new runs |
| Actions per day | `max_actions_per_day` | Desktop actions, the direct proxy for desktop-seconds |
| Wall seconds per day | `max_wall_seconds_per_day` | Total desktop occupancy |
| Model tokens per day | `max_model_tokens_per_day` | Spend |
| Concurrent runs | `max_concurrent_runs` | Simultaneous desktop occupancy |

Exhaustion is a `QUOTA_EXHAUSTED` refusal (429), not a queue. A quota that queued would turn a
budget control into a latency control, and the run would still eventually cost the money.

**There is no NULL meaning "unlimited".** An unbounded action count is an open-ended licence to drive
somebody's desktop, and an unbounded token count is an open-ended licence to spend their money.

---

## 3. Proposed instance classes

Sized against contracts proven locally, not against a load test. No load test has been run.

| Component | Proposed | Basis |
|---|---|---|
| API | ECS Fargate, 2 × (1 vCPU, 2 GiB) | Two tasks so a rolling deploy is not an outage. The API is I/O-bound on PostgreSQL. |
| Migrator | Fargate run-once, 0.5 vCPU, 1 GiB | Runs alone, before the service updates. |
| PostgreSQL | `db.t4g.medium`, Multi-AZ, 100 GiB gp3 | A starting point, explicitly. Right-sizing needs a real workload. |
| Desktop runners | **Not proposed.** | AWS has no service that provides a real interactive desktop with a working screen reader. EC2 Mac is the only candidate for VoiceOver and whether it can be driven under automation with the required Accessibility and Automation grants is unverified here. Proposing an instance type would imply it had been tried. |

---

## 4. Where a bill would surprise you

Written down because each of these has a specific mitigation, and the mitigation is worth having
before the invoice rather than after.

| Surprise | Mechanism | Mitigation |
|---|---|---|
| One workspace consumes the fleet | Concurrency is a physical resource; a workspace with a high `max_concurrent_runs` starves the rest | Set it deliberately per workspace. It is not a soft limit. |
| A retry loop spends a day's tokens in an hour | Per-action model calls with no per-run ceiling | `max_model_tokens_per_day`, plus an AWS Budgets alarm on the account |
| Storage grows without bound | Evidence is never overwritten and versioning is on | Retention policy per workspace, and a **separate** decision about expunging old versions |
| A hung run holds a desktop for hours | Lease deadline is the only backstop | Keep the lease deadline short; a supervisor that cannot heartbeat should lose its lease quickly |
| Cross-AZ data transfer | Fargate in one AZ reading S3 and RDS in another | Same-region, and gateway endpoints for S3 |
| The backup itself | Every evidence object is copied on every full backup | Incremental backup is **not implemented**. A full backup of a large evidence store will be expensive, and this is a known limitation rather than a solved problem. |

---

## 5. What has not been measured

Stated plainly, because the sections above would otherwise read as if they were derived from
observation.

- No run has been executed against a real screen reader. Desktop-seconds per run: **unknown**.
- No model has been invoked. Tokens per run: **unknown**.
- No load test has been run. Requests per second per task: **unknown**.
- Nothing has been deployed to AWS. Every instance class above is a proposal.

A capacity plan or a budget built on this document must treat the four unknowns as unknown.
