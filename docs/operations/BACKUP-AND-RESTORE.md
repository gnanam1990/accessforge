# Backup and restore

A backup that has never been restored is a hypothesis. Everything here has been executed against a
real PostgreSQL 17 and a real S3-compatible store, and the results are in
[docs/handoffs/27.md](../handoffs/27.md).

---

## 1. What a backup contains, and what it cannot

`scripts/backup.py` produces one encrypted file containing four things:

| Member | Contents |
|---|---|
| `postgres.dump` | `pg_dump --format=custom`, **with** ownership and privileges |
| `evidence/…` | Every object in the evidence bucket, enumerated with pagination |
| `configuration.json` | Every required variable **by name**. No values. |
| `keys.json` | Signing key ids and issuers. **No private key material.** |
| `MANIFEST.json` | A SHA-256 of every member, the schema state, and an explicit list of omissions |

### Ownership is not optional

`pg_dump --no-owner --no-privileges` is the reflex for moving a database between environments, and
here it produces a restore the application role **cannot read at all**. Tenant isolation in this
system is expressed as table ownership plus `FORCE ROW LEVEL SECURITY`; a dump that discarded
ownership restores tables owned by whoever ran the restore, with no grants.

Asserted by `test_a_dump_that_discards_ownership_produces_an_unusable_restore`.

### The backup role must bypass row-level security

`FORCE ROW LEVEL SECURITY` applies to the table owner. The application role owns the tables, so
`pg_dump` run as the application role **fails** — it cannot read its own rows.

A backup therefore needs a superuser or a role with `BYPASSRLS`. This is an operational fact, not a
test detail: a backup script running as the application role produces no usable backup and finds out
at restore time. Asserted by `test_the_application_role_cannot_take_a_backup`.

### What it cannot contain

Recorded in `MANIFEST.json` so an operator reads it from the file rather than discovering it during
an outage:

- **Private signing key material.** It lives in a key management service. The public halves and key
  ids are here, which is what a reader verifying an export needs.
- **Credentials.** A backup holding the credential for the store it was copied to is a backup that
  decrypts itself.
- **Anything deleted before the snapshot.** A restore does not undo a retention deletion.
- **The state of any physical desktop runner.**

---

## 2. Encryption

AES-256-GCM, framed in 1 MiB chunks. The framing is the part that matters:

| Attack | What stops it |
|---|---|
| Truncate the tail | Every chunk carries its index **and** a final-chunk flag in the AAD; a stream ending without a final chunk is refused |
| Reorder two chunks | The index is authenticated |
| Splice chunks from another backup taken with the same key | A per-archive random `stream_id` is in the header, and the header is in every chunk's AAD |
| Nonce reuse | 4-byte random stream prefix + 8-byte counter; the prefix is recorded in the header so reuse is *checkable* rather than assumed |

Each of these is a test in `tests/unit/test_backup_envelope.py` that performs the attack on a real
sealed archive.

The header records the **key id** and no key material, so an operator holding only the file can
learn which key to fetch. "Try every key you have" is not a recovery procedure, and a format that
forced it would make rotation a reason not to rotate.

---

## 3. Taking a backup

```bash
# Once, ever. Store a copy somewhere this machine is not.
uv run python scripts/backup.py --write-new-key --key-file /secure/backup.key --output /dev/null
```

```bash
ACCESSFORGE_BACKUP_DATABASE_URL='postgresql://backup-role@host:5432/accessforge' \
uv run python scripts/backup.py \
  --output /backups/accessforge-$(date -u +%Y%m%dT%H%M%SZ).afbk \
  --key-file /secure/backup.key \
  --key-id af-backup-2026-09
```

The output is mode 600 and names its omissions on stdout. `--skip-evidence` takes the database only
and records the omission in the manifest, so a restore from it cannot be mistaken for a complete one.

---

## 4. Restoring

**Restore into a disposable database. Never over the source.** The target is compared against the
manifest's recorded source by host, port and database name — not by string, so a different spelling
of the same database is still caught — and a match is refused.

```bash
# What is in this file? Answered without touching a database.
uv run python scripts/restore.py --archive /backups/….afbk --key-file /secure/backup.key \
  --target-database-url 'postgresql://unused@localhost/unused' --inspect
```

```bash
createdb accessforge_recovery
ACCESSFORGE_EVIDENCE_ENDPOINT_URL=… ACCESSFORGE_EVIDENCE_ACCESS_KEY=… ACCESSFORGE_EVIDENCE_SECRET_KEY=… \
uv run python scripts/restore.py \
  --archive /backups/….afbk \
  --key-file /secure/backup.key \
  --target-database-url 'postgresql://superuser@host:5432/accessforge_recovery' \
  --target-bucket accessforge-evidence-recovery \
  --operator "$(whoami)"
```

The target must be **empty**. A restore over existing tables stops part-way and leaves neither the
old contents nor the new; the script checks and refuses before writing anything.

The connection must bypass row-level security. Reconciliation on an unscoped application connection
sees **zero rows**, so every `UPDATE` would report success and change nothing — an operator following
the runbook, seeing no error, and serving a database full of valid credentials. `assert_can_reconcile`
refuses any weaker role rather than running and doing nothing.

---

## 5. Reconciliation — what a restore invalidates

Runs automatically unless `--no-reconcile`. It is destructive in exactly one direction: **it
invalidates authority and preserves evidence.**

| Invalidated | Why |
|---|---|
| Every session | A token minted before the snapshot may have been revoked after it, and the revocation is not in this data |
| Every unredeemed enrollment token | Grants desktop input authority, single-use, and may already have been redeemed on a machine this database cannot see |
| Every lease | Released with reason `RESTORED_DATABASE` — not reassigned. The previous supervisor may still be driving a desktop this database can no longer see. |
| Every runner | Quarantined. A quarantine is released by a trusted reset and a fresh preflight, which is exactly the proof a restore cannot supply. |
| Every claimed job | Released to `PENDING`, not deleted. The claim is stale; the work is not. |
| Every undelivered outbox row | Marked published without delivery. Redelivering a backup's worth of announcements tells the world a day of events is happening again right now. |

| Preserved | Why |
|---|---|
| Every terminal run's status and outcome | Terminal records are immutable by trigger. A restore that "tidied" an interrupted run into a completed one would be a false PASS produced by an operator holding a backup. |
| Every evidence record and artifact | The thing the backup exists for |
| Every audit row | |
| Ambiguous attempts | Quarantined and left ambiguous. An action dispatched with no recorded result may have taken effect, and a restore is not evidence that it did not (INV-09). |

**Execution grants are listed, never trusted.** A grant revoked after the snapshot is live in the
backup and revoked in the world, and this data cannot tell the difference. Every restored grant
requires explicit revalidation by a person before anything is dispatched under it. Fail-closed is
the only available answer.

**Reconciliation runs once.** A second pass would fence leases granted legitimately after the first
and quarantine runners somebody had just reset. Once is a recovery step; twice is an outage. The
refusal is recorded in `global_audit_event`.

---

## 6. Migration and rollback limits

Startup does **not** migrate. Deploy order is fixed:

1. Take a backup — a migration is the change a restore exists for
2. `uv run python scripts/migrate.py`
3. Start the new binaries

`/health/ready` reports the result of step 2 rather than performing it, so a process that came up
against an un-migrated database answers 503 with the reason instead of serving.

**Forward only.** A restore whose schema is *ahead* of the code is refused rather than downgraded:

- A code rollback does not reverse a data migration.
- Old code against a newer schema reads columns it does not know about as **absent**, which is
  indistinguishable from a column being empty. That is how a rollback silently discards data.
- A code rollback does not restore deleted evidence.

The recovery for "we rolled the schema too far forward" is to bring the code forward, or to restore
the backup taken in step 1 into a disposable database and reconcile it.
