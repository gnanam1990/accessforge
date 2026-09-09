# Prompt 05 — Authorized projects and immutable build manifests

**Dependencies:** 03, 04. **Requirements:** FR-001, FR-002, FR-010, FR-014. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), PRD scope, TDD build identity and TEST-PLAN. Registering a repository is not authorization to execute its scripts or publish changes.

## Copy-paste prompt

```text
Implement AccessForge module 05 only.

Objective: Connect a project to an explicitly authorized source/environment and freeze the exact bytes used in a run, without allowing mutable URLs or branch names to masquerade as reproducible evidence.

Owns: project/environment registration services, immutable source/build manifest services, project repositories under packages/persistence/, source intake interfaces and their tests. Arbitrary build execution belongs to module 14; GitHub publication belongs to module 20.

Required inputs: Completed identity and journal handoffs. Use the canonical RunManifest fields and digest rules. Project records must bind workspace, repository authorization, environment owner, permitted effects and credential-profile references without storing secret values in public manifests.

Tasks:
1. Implement project creation, environment registration, owner authorization and explicit revocation using current memberships and revisions. Permit only owned/authorized local or staging environments for E0/R1 automated effects.
2. Resolve repository revisions to immutable commit and tree identities. Detect dirty local source; either snapshot it with an explicit digest/provenance or refuse reproducibility claims. Never label changed working-tree content as clean HEAD.
3. Define approved source intake without automatically executing package hooks, Git hooks, submodules or generated instructions. Require an explicit allowlist and isolation for any later execution.
4. Capture sourceTreeDigest, buildArtifactDigest and environmentConfigDigest separately. A branch name, image tag or deployment URL is not an immutable build identity.
5. Create environment manifest records for target origin, browser exposure, fixture reset strategy and independent receipt observer. Normalize origin rules and prevent redirects or changed deployment routing from broadening authority.
6. Separate fixture-value access for navigator from reset/oracle credentials for trusted services. Store credentials in an appropriate secret store with narrow consumers; manifests contain stable references only.
7. Implement sealing against exact journey, assertions, fixture, runner, evaluator, model and run-effects authorization. For grant-derived runs, allocate run/authorization IDs before sealing, mint the exact child with parent revision, and reject absent, changed or unauthorized inputs. Never use an ExecutionGrant itself as exact run approval.
8. Revalidate source/build/environment identity and any parent grant immediately before dispatch. A changed deployment requires a new sealed run and exact child authorization, not a warning or mutation of an existing approval.
9. Provide list/detail/history operations with immutable snapshots and append-only supersession. Deletion/revocation makes future use unavailable without rewriting prior evidence identities.
10. Expose safe capability summaries for downstream journey authoring and patch preparation: supported origin, fixture reset contract, source access scope and whether build identity is actually observable.

Negative tests: Force-pushed branch, dirty checkout, artifact substitution under the same URL, poisoned archive path, symlink escape, unauthorized repository, cross-workspace environment, redirect to production, leaked receipt token and environment change after approval (INV-03, INV-07, INV-08). Demonstrate an unidentifiable external deployment cannot produce a fully verified provenance claim.

Acceptance: Given one authorized local reference project, the service seals deterministic source/build/environment identities and detects changed bytes before dispatch. No source-intake operation executes repository code or publishes content. Unsupported identity capture remains visible rather than being filled with an invented digest.

Stop conditions: Missing ownership, mutable unobservable deployment identity or unsafe source access blocks that environment. Continue metadata/UI contract work but do not use public reachability as consent.

Handoff: Write docs/handoffs/05.md with immutable example manifests, threat tests, credential separation evidence and interfaces ready for modules 06 and 14. Record what is local proof versus a future hosted integration.
```
