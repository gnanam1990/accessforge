# AccessForge Tester Desk · Team handoff and validation

Create `build-progress/accessforge-tester-desk/` in the target project and copy [the handoff template](templates/HANDOFF.md) to `HANDOFF.md` and [the initial status](templates/STATUS.json) to `STATUS.json` once. Preserve existing progress on subsequent sessions. Keep the source pack under version control with the target repo when authorized. A cloud agent needs the latest project checkout, progress files and relevant local changes to resume. Uploading the original ZIP again gives it the design baseline only.

## Team interfaces

Assign a single owner per module and record active branches to avoid competing migrations. Domain and persistence owners define versioned contracts. Provider and worker owners maintain external reconciliation. UI and QA owners exercise the same API paths as end users. Agree payloads, error states and fixture provenance at interface boundaries. Parallelize tests, adapter conformance and UX work that can proceed against agreed contracts. Reconcile integration to one tested revision.

## At each session boundary

1. Record module ID, branch, tested code SHA, working-tree changes, dependency decisions and open external gates. List commands exactly, with environment, exit code, test count and redacted evidence path.
2. Record each module as pending, active, blocked or accepted in the mutable status file. Accept only when the implementation proof exists. Leave a failed test and a single next action for partial work. Update handoff and status together; use a focused local progress commit where authorized.
3. The incoming agent resolves the recorded SHA, checks ancestry against current HEAD, inspects pending diffs, and reruns a targeted check. If divergent, reconcile state before continuing. A moved branch, private source denial, failed provider operation or missing evidence stays explicit.

## Release proof

Validate from a separate clean checkout of the exact candidate revision. Run the repository's native install, migrations and checks, start API and worker and use the normal UI or documented API. Save commands, exit codes and final observed state. A local proof and a live external proof have distinct statuses. Record the module's tests and a [decision](templates/DECISION.md) for new assumptions.
