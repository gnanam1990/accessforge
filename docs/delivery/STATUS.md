# Delivery status

**Last refreshed:** 2026-09-10 (module 04 delivery)
**Repository:** `github.com/gnanam1990/accessforge` (public)
**Target branch:** `main`

Refresh this from live Git and CI state, not from a previous checkbox.

## Four separate claims

| Claim | Answer |
|---|---|
| Is code merged? | Modules 00 through 03 are merged. Module 04 (journal and outbox) is **open in a pull request**, not yet merged. |
| Is a runtime verified? | **Partly.** The reference application and control-plane API were started and exercised over real HTTP against real PostgreSQL, including restart durability, and tenant isolation was proved by direct SQL. **No screen reader has ever run.** |
| Is R1 release-ready? | **No**, and it cannot become ready on this host — module 09 requires Windows/NVDA. |
| Did deployment or event submission occur? | **No.** Neither is authorized. |

## Main

| Item | Value |
|---|---|
| Bootstrap commit | `7576c05` — specification pack and repository hygiene only |
| Module 00 integration commit | `fc00eb8` — verified on main |
| Module 01 integration commit | `23fe6c8` — verified on main, CI green |
| Module 02 integration commit | `dbca481`, plus follow-up fix `bb52c30` — verified on main |
| Module 03 integration commit | `141e3de` — verified on main, CI green with RLS genuinely enforced |
| Latest verified integration commit | `141e3de` |
| CI on main | **Passing** for `23fe6c8`. Three jobs: Python (real PostgreSQL), Node, documentation integrity. |

## Modules

Module 00: **merged** at `fc00eb8`, verified on main.
Module 01: **merged** at `23fe6c8`, verified on main with a clean-checkout smoke.
Module 02: **merged** at `dbca481`, verified on main; a post-merge defect was fixed in `bb52c30`.
Module 03: **merged** at `141e3de`, verified on main. Partial by design — the authorization
primitives are complete, but no HTTP surface exposes them until module 18.
Module 04: **implemented and locally verified** (659 Python tests including a crash matrix and real
two-connection concurrency), delivery **open**.
All other modules: not started.

See `docs/delivery/PLAN.md` for the full ledger.

## Post-merge findings

A second entry was added after module 03: CI ran the entire tenant-isolation suite against a
PostgreSQL **superuser**, which bypasses row-level security including FORCE. Sixteen assertions failed
at once — the correct outcome, but a poor diagnosis. CI now uses a NOSUPERUSER NOBYPASSRLS role and
`assert_row_level_security_enforced` fails with one sentence if that regresses.


| Found at | Issue | Status |
|---|---|---|
| `dbca481` (module 02) | `pnpm -r test` failed from a clean clone: the Node test scripts import from `dist/` but did not build it. CI masked this by running `build` before `test`. | fixed in a follow-up PR |

Caught by the clean-clone smoke rather than by CI, which is the point of running it: CI's step
ordering made a broken standalone command look fine.

## Open blockers

| Blocker | Owner action required | Blocks |
|---|---|---|
| VoiceOver AppleScript control disabled and never configured | Enable in VoiceOver Utility → General; grant Accessibility + Automation to the controlling terminal/IDE; provide a dedicated desktop session | 08, 12, **all E0 acceptance** |
| No Windows host or virtualization software | Provide a Windows machine or VM with NVDA | 09, **full R1** |
| No AWS credentials or Bedrock access | Configure credentials **and** approve billable model invocation separately | 12, 13, 14 real-model proof |
| No authorized target application | Name an application you control or are authorized to test, with permitted effects | 05, 08, **all E0 acceptance** |
| No object store running | Start Colima and run MinIO, or supply an S3-compatible endpoint | 10, 17 real-artifact proof |
| Branch protection not configured | Owner configures protection rules; the agent does not change repository settings | guarded merge strength |

## Outstanding debts

- Executable negative-verification tests now exist for the fixture and configuration guards, proved
  by mutation in module 01. The *reader-specific* case — an unconfigured screen reader must yield
  BLOCKED/INCONCLUSIVE and never PASS — still has no executable test, because no runner exists yet.
  Owed by module 08.
- Test-first ordering was not followed in module 01; guards were mutated afterwards to prove the
  tests are falsifiable. See `docs/handoffs/01.md`.
