# Operator runbooks

Each runbook says how to stop safely, and — separately — what must be **verified** before restarting.
The two are deliberately not one step. "Stop it and start it again" is how a system with an
unresolved action dispatches that action twice.

Every runbook that ends in a restart names the evidence required. Where that evidence cannot be
obtained, the runbook says the honest thing rather than offering a workaround.

---

## Safe stop — the shared preamble

Applies to every procedure below that involves stopping something.

| Component | Safe stop | Why |
|---|---|---|
| API task | `SIGTERM`, then wait for drain | Uvicorn stops accepting connections and waits for in-flight requests. Every mutation is a single database transaction, so an interrupted request rolls back and the caller's `Idempotency-Key` makes the retry exact. There is nothing to clean up. |
| Migrator task | **Do not kill mid-migration.** Wait. | Each migration runs in its own transaction and is recorded in the same transaction, so a killed migrator leaves neither a half-applied schema nor a false record of success. But a killed migrator *during* a long `ALTER` leaves a lock, and the next attempt blocks behind it. |
| Job worker | `SIGTERM` between jobs | A worker killed before committing its claim loses nothing — the claim rolls back and the job returns to `PENDING`, attempt counter and all. A worker killed *after* committing its claim leaves a stale claim, which expires and is reclaimable. Both are recoverable by state; neither requires knowing what the dead process was doing. |
| Desktop supervisor | **Never `SIGKILL` while an action is in flight.** | This is the one place where killing a process costs correctness. An action dispatched with no recorded result may have taken effect on a real machine. See *Quarantine* below. |

---

## 1. Certificate or token rotation

### Session signing / CSRF material

**Stop:** none required. Rotation invalidates existing sessions; users sign in again.

**Do:**
1. Write the new value into the secret store.
2. Restart API tasks one at a time.
3. Confirm `/health/ready` returns 200 on each before moving to the next.

**Verify before declaring done:** a fresh sign-in succeeds, and a session cookie captured before
rotation is rejected. If the old cookie still works, the rotation did not take effect on that task.

### Runner enrollment tokens

Single-use and time-bounded. They grant desktop *input* authority, which makes them the highest-value
credential in the system.

**Do:** issue a new token; the old one expires on its own schedule. There is no revocation column —
expiry is the mechanism the table has, and adding a second one would give the redemption path two
conditions to check and one to forget.

**If a token may have leaked:** expire it immediately by setting `expires_at` to now, then treat every
runner enrolled since it was issued as untrusted (§8).

### Evidence signing key

**Do not delete the old key.** A bundle signed last month must stay checkable after the key changes.
`Attestation.key_id` travels with the bundle precisely so that a verifier holding only the current
key does not report every older bundle as forged.

1. Generate the new key in the key management service.
2. Update `ACCESSFORGE_SIGNING_KEY_ID`.
3. Publish the new public key where readers obtain keys.
4. **Keep the old public key published indefinitely.**

**Verify:** an export produced after rotation verifies against the new public key, and an export
produced before it still verifies against the old one.

### Backup archive key

**Rotating this key does not re-encrypt existing backups.** Every archive sealed with the old key
needs the old key, forever, or it is unreadable. `scripts/backup.py --write-new-key` refuses to
overwrite an existing key file for exactly this reason.

1. Write the new key to a **new** path.
2. Point `ACCESSFORGE_BACKUP_KEY_FILE` at it.
3. Retain the old key for at least the backup retention period.
4. Take a fresh backup and **restore it into a disposable database** before trusting the new key.

---

## 2. Reader (screen reader) upgrade

A screen reader upgrade changes what the product observes. Announcements are the evidence; a
different version can phrase them differently.

**Stop:** drain runs on the affected runners. Do not upgrade a machine holding a lease.

**Do:**
1. Quarantine the runner (it will not be leased).
2. Upgrade the reader.
3. Re-run preflight. A new reader version means a new `profile_digest`.

**Verify before returning it to service:** preflight passes on the new version **and** a known
journey produces the expected observations. A reader upgrade that changes phrasing will change
verdicts, and a fleet upgraded silently produces a step-change in results that nobody can attribute.

**Do not** return a runner to service on the strength of the upgrade completing. The upgrade
completing is evidence about the installer.

---

## 3. Quarantine — a runner or attempt with an unresolved action

The condition: an action was dispatched and no result was recorded. The machine may have performed
it. Nothing in this system can establish otherwise (INV-09).

**Do NOT:**
- Mark the run failed and move on. The action may have taken effect.
- Re-dispatch the action. That is the double-action this invariant exists to prevent.
- Lease the desktop to another attempt. Two actors on one machine.

**Do:**
1. Confirm the attempt is quarantined (`run.quarantined`) and the lease is released.
2. **Go and look at the machine.** This is a physical question and the answer is not in the database.
3. Record what was found in the run's ambiguity reason.
4. Reset the desktop to a known state — a trusted reset, not a best guess.
5. Run preflight.
6. Only then clear the quarantine.

**Verify before restart:** preflight passes *and* a person has confirmed the desktop's actual state.
Step 2 has no automated substitute, and a runbook that pretended otherwise would be the product
making exactly the claim it exists to refuse.

---

## 4. Queue backlog

**Diagnose first.** A backlog is a symptom with several causes and the wrong fix makes each worse.

| Observation | Cause | Action |
|---|---|---|
| Jobs `PENDING`, no workers claiming | Workers down | Restart workers. Nothing is lost — the job rows are in PostgreSQL. |
| Jobs `CLAIMED` with expired claims | Workers died after committing claims | Claims expire and are reclaimable. If they are not being reclaimed, the workers are running but wedged. |
| Jobs `CLAIMED`, claims fresh, nothing completing | Workers are stuck on something | Look at `job.last_error` and `attempts`. Do not restart blindly; a job that fails identically after five attempts will fail identically after fifty. |
| Runs `QUEUED`, no desktops free | Not a queue problem | This is capacity. Concurrency is bounded by physical desktops. |
| `unpublished_count` rising | Outbox not draining | The outbox is the source of truth; the queue is a wake-up signal. Messages are not lost. |

**Never** delete jobs to clear a backlog. A job is a database operation and re-reading state makes a
repeat harmless; deleting it loses the work with no record that anything was dropped.

**Never** raise `max_concurrent_runs` to clear a run backlog. It is bounded by machines, not by a
number in a table, and raising it produces failed leases instead of completed runs.

---

## 5. Failed migration

**Stop:** do not start new binaries. `/health/ready` returns 503 with the reason, so tasks that come
up take no traffic — that is the designed behaviour, not an outage to work around.

**Do:**
1. Read the error. Each migration is transactional, so the failed one applied nothing and is not
   recorded.
2. If it failed on a lock: find the blocking session (`pg_stat_activity`), and understand *why* it
   is holding the lock before terminating it.
3. If it failed on data: the migration is wrong. Fix it in the tree. Do **not** hand-edit the
   database into a state the migration would have produced — `schema_migration` would then record a
   migration that never ran, and the next deploy applies later ones on top of a schema nobody
   described.
4. Re-run `scripts/migrate.py`.

**If the migration partially succeeded across several migrations:** the ones that committed are
recorded and will not re-run. Fix the failing one only.

**Rollback is not symmetric.** A code rollback does not reverse a data migration. If the schema is
now ahead of the binaries you want to run, the recovery is to bring the code forward, or to restore
the pre-migration backup into a disposable database and reconcile it — see
[BACKUP-AND-RESTORE.md](BACKUP-AND-RESTORE.md) §6.

**Verify before restart:** `scripts/migrate.py --check` exits 0, and `/health/ready` reports the
expected migration.

---

## 6. Model unavailable

**Symptom:** `DEPENDENCY_UNAVAILABLE` (503) on run admission, or agent actions failing.

**Do NOT:** fall back to a different model, or to no model, and continue producing verdicts. A run
whose reasoning came from somewhere other than the configured model is not the run that was
requested, and a verdict produced without one is not a verdict.

**Do:**
1. Confirm the outage is upstream (region status, credentials, quota).
2. Let in-flight runs fail as `INCONCLUSIVE`. That is the honest outcome.
3. Do not admit new runs until it is back.

**Verify before resuming:** an actual model call succeeds. A restored credential is not a restored
service.

---

## 7. Object storage unavailable

**Symptom:** readiness fails on `evidence-store`; artifact uploads fail.

**There is no filesystem fallback, by design.** Evidence written somewhere other than the evidence
store is evidence with no retention policy, no integrity check and no path into an export.

**Do NOT:** allow runs to proceed and "attach the artifacts later". A run that finished without its
artifacts is a run whose evidence is missing, and the completeness assessment will say so —
correctly — for as long as it stays missing.

**Do:**
1. Stop admitting runs.
2. Restore the store.
3. Verify stored integrity for artifacts written near the outage: an object can be swapped
   underneath a row that still records the old digest, which is why promotion re-reads the bytes.

**Verify before resuming:** a real object round-trips, and `verify_stored_integrity` reports no
mismatches for the affected window.

---

## 8. Suspected compromise of a runner

**Stop immediately.** Do not drain — a compromised runner holding a lease is a compromised runner
driving a real machine.

1. Revoke the runner (`revoked_at`).
2. Expire every unredeemed enrollment token for that workspace.
3. Treat every run that used that runner as untrusted evidence, and say so in the record rather than
   deleting it. Deleted evidence looks identical to evidence that never existed.
4. Rotate the workspace's credentials.

**Restart condition:** a rebuilt machine, a new enrollment token, a fresh preflight, and a person's
decision. Not a cleared flag.
