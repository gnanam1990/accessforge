# Delivery status

**Last refreshed:** 2026-09-10 (module 07 merged; verified on main)
**Repository:** `github.com/gnanam1990/accessforge` (public)
**Target branch:** `main`

Refresh this from live Git and CI state, not from a previous checkbox.

## Four separate claims

| Claim | Answer |
|---|---|
| Is code merged? | **Modules 00 through 07 are all merged**, each verified on main with CI green. Module 08 is the next in dependency order and is blocked on the owner, not on code. |
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
| Module 04 integration commit | `9a1aef6` — verified on main |
| Module 05 integration commit | `3977e18` (merge of `a37da63`) — CI green on the pull request |
| Module 06 integration commit | `85e5935` (merge of `955f815`) — CI green, seven independent-review findings closed |
| Module 07 integration commit | `0c88f49` (merge of `fc3505c`) — CI green on main, seventeen mutation checks |
| Latest verified integration commit | `0c88f49` |
| CI on main | **Passing** for `0c88f49`. Three jobs: Python (real PostgreSQL, non-superuser role), Node, documentation integrity. |

## Modules

Module 00: **merged** at `fc00eb8`, verified on main.
Module 01: **merged** at `23fe6c8`, verified on main with a clean-checkout smoke.
Module 02: **merged** at `dbca481`, verified on main; a post-merge defect was fixed in `bb52c30`.
Module 03: **merged** at `141e3de`, verified on main. Partial by design — the authorization
primitives are complete, but no HTTP surface exposes them until module 18.
Module 04: **merged** at `9a1aef6`, verified on main.
Module 05: **merged** at `3977e18`, CI green on the pull request.
Module 06: **merged** at `85e5935`, with seven findings from the independent review fixed and
mutation-proven.
Module 07: **merged** at `0c88f49`, verified on main. 1016 Python tests and 82 Node tests on a
database created from nothing, seventeen mutation checks. Real PostgreSQL concurrency and a real
`fsync`ed action journal; **no screen reader, and no operating-system fencing demonstrated** — see
`docs/handoffs/07.md` for all seven unverified boundaries by name.
All other modules: not started. **Module 07 is the last module buildable on this host** — 08 and 09
require a real screen reader, and everything from 10 onwards depends on evidence only they can
produce.

See `docs/delivery/PLAN.md` for the full ledger.

## Post-merge findings

A second entry was added after module 03: CI ran the entire tenant-isolation suite against a
PostgreSQL **superuser**, which bypasses row-level security including FORCE. Sixteen assertions failed
at once — the correct outcome, but a poor diagnosis. CI now uses a NOSUPERUSER NOBYPASSRLS role and
`assert_row_level_security_enforced` fails with one sentence if that regresses.


| Found at | Issue | Status |
|---|---|---|
| `dbca481` (module 02) | `pnpm -r test` failed from a clean clone: the Node test scripts import from `dist/` but did not build it. CI masked this by running `build` before `test`. | fixed in a follow-up PR |
| module 06, pre-merge | Two defects passed locally and failed in CI solely because the local test database was carried over from module 05. A branch cut before module 05 shipped a migration referencing a table module 05 creates, and a regression test asserted two table names were present when only one of them was the product's. | both fixed; `migrate` now refuses a gapped series and a database recording migrations absent from the tree, and the verification procedure recreates the database |
| module 06, pre-merge | `packages/persistence/src` — the package holding every tenancy decision — was absent from CI's strict `mypy` targets. It already passed; nothing had been checking. | added to the type-check step |
| module 04, found during module 07 | `run.lease_epoch` was written by every transition and set by no reducer, so a run's epoch stayed 0 for life. `acknowledge_stop` refuses an acknowledgement whose epoch does not match the current one, so it would have refused **every acknowledgement that could ever exist** — no run holding a desktop could reach terminal CANCELLED. Module 04's tests passed because they acknowledged at epoch 0. | `admit_to_desktop` reducer added; `admit_lease` applies it in the transaction that grants the lease |
| module 07, pre-merge | `revoke_runner` retired a registration while it still held a live lease, creating the only state in which two runner rows can name one desktop. Found because removing an advisory lock failed no tests, which prompted asking what the lock was for. | revocation refused while a lease is active; the advisory lock removed as indistinguishable from the unique index that is the actual guarantee |
| module 07, pre-merge | The automated reviewer was rate limited, so the independent review was done by hand on green CI. It found two more: the ambiguity constraint was one-directional, permitting an AMBIGUOUS action with no reason — a known-bad result that tells an operator nothing, and the exact state a caller reaches by bypassing the function that also quarantines the desktop. And `record_action_intent` trusted the caller's epoch, which no schema constraint could catch because the foreign key binds the lease and not its epoch. | constraint made bidirectional; epoch read from the lease and compared, and a released lease refused outright |

Caught by the clean-clone smoke rather than by CI, which is the point of running it: CI's step
ordering made a broken standalone command look fine.

## Open blockers

| Blocker | Owner action required | Blocks |
|---|---|---|
| VoiceOver AppleScript control disabled and never configured | Enable in VoiceOver Utility → General; grant Accessibility + Automation to the controlling terminal/IDE; provide a dedicated desktop session | 08, 12, **all E0 acceptance**, and **every operating-system boundary module 07 leaves UNVERIFIED** |
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
- `mypy` still does not cover `tests/`, which reports 139 strict errors — almost all of them bare
  `dict` annotations. That is a real gap in a suite whose correctness is the evidence for everything
  else, and it is untouched rather than unknown.
- `fixture_digest` is an unkeyed SHA-256 over fixture and observer values, several of which have low
  entropy, so anyone holding an export can test offline guesses at what a run was checked against.
  Raised by the module 06 independent review; a keyed commitment was refused because it would make
  exports unverifiable offline, which CONTRACTS requires. The reasoning is in `docs/handoffs/06.md`
  and the trade-off is open, not settled.
- The silent table collision found in module 06 can no longer be reproduced: the two tables now have
  incompatible schemas, so a misdirected write fails loudly. The replacement test proves the product
  uses its own table; it does not reconstruct the silence. See `docs/handoffs/06.md`.
