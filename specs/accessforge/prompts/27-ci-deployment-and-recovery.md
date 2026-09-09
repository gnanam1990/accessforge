# Prompt 27 — Reproducible CI, deployment preparation and recovery proof

**Dependencies:** 26  
**Requirements:** FR-004, FR-014, FR-015, FR-020–FR-022, FR-025; INV-03, INV-06–INV-11, INV-13–INV-15  
**Owns:** CI configurations, local deployment, authorized-host deployment plans, migrations, backup and recovery procedures

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), [SECURITY-PRIVACY.md](../SECURITY-PRIVACY.md), deployment sections in [TDD.md](../TDD.md) and [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 27 only.

Objective: Make the selected release installable and recoverable on a clean machine. Preparing configuration is authorized; creating cloud resources, changing remote CI settings or deploying to a user's account requires specific user approval.

Inspect actual lockfiles, runtime support, migration history, container boundaries and worker platform requirements. Do not pretend a Linux container can provide actual VoiceOver/NVDA. Record the exact version matrix and required interactive desktop permissions.

Implementation tasks:
1. Provide reproducible development/bootstrap commands using pinned tested dependencies and validated environment contracts. Check missing credentials/capabilities up front without printing secrets or silently downloading unverified binaries.
2. Configure CI stages for schema generation/diff, formatting, static analysis, units, real PostgreSQL integration, API/client contracts, UI, security and evidence verifier. Do not mark platform-only tests passed when runners are unavailable.
3. Separate trusted release workflows from untrusted repository/PR builds. No broad credentials in forked CI, package lifecycle scripts or candidate execution; scope cache and artifact reuse by trustworthy identities.
4. Implement startup readiness, migration ordering and graceful shutdown. Readiness must check required services and schema compatibility without claiming a disconnected physical runner is ready.
5. Create proposed AWS deployment/IAM/storage/queue configurations matching the proven local contracts. Keep Strands/model configuration explicit; AgentCore or hosted transports require separate actual verification before deployment claims.
6. Document expected resource classes, model/desktop cost drivers, quotas and budget controls without inventing current prices. Show the exact proposed external changes and obtain approval before provisioning or deployment.
7. Build encrypted backup and restoration procedures for PostgreSQL, object evidence, configuration and supported key metadata. Retention/deletion rules and signature verification must survive restore coherently.
8. Restore into an isolated local/test target. Invalidate old leases/credentials, fence stale workers and preserve missing-stop quarantine. Revalidate ExecutionGrants and exact child approvals; restored schedules/outbox rows cannot replay completed/ambiguous actions or silently resurrect revoked authority.
9. Test forward migrations from the supported previous schema and define rollback/data-compatibility limits. A code rollback does not automatically reverse a data migration or restore deleted evidence.
10. Create operator runbooks for certificate/token rotation, reader upgrade, quarantine, queue backlog, failed migration and unavailable model/storage. Include safe stop and separately verified restart conditions.

Required verification:
Perform a clean install and a real backup/restore drill using disposable authorized local resources. Kill API/worker during migration/outbox processing, restore with stale pending jobs, and verify terminal immutability, tenant isolation, evidence integrity and no OS action replay. Run actual pinned-platform smoke tests where available. CI YAML, infrastructure plans and simulated cloud responses are not hosted execution evidence.

Acceptance gate:
A fresh environment runs the complete declared local product path, and an isolated restore preserves the authoritative state without duplicating actions or claiming missing evidence. Hosted deployment remains PREPARED or BLOCKED until explicitly authorized and actually tested.

Handoff:
Write docs/handoffs/27.md with environment/version inventory, clean-install and restore commands/results, measured limitations and pending external permissions. Do not push, deploy or incur charges merely to improve the handoff status. Stop after this module.
```
