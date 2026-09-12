# Python dependency scanning in CI

Closes P5. The JavaScript dependency tree has been audited in the security job since module 01
(`pnpm audit --audit-level=high`); the Python tree had nothing, which is the larger surface — the API,
the persistence layer, the evidence tooling and the navigator all run on it.

## What the gate does

`scripts/audit_python_dependencies.py`, run as the **first** step of the security job:

1. `uv export --frozen --format requirements-txt --no-emit-workspace` renders `uv.lock` to a fully
   pinned, hash-bearing requirements set, written to a temporary file and removed afterwards.
   pip-audit needs a path, but the rendered lock is derived state: committing it would create a
   second source of truth that goes stale silently, and a reviewer comparing the two could not tell
   which one the gate had used. It is gitignored as well as deleted.
2. `pip-audit --no-deps --disable-pip --strict --format json` audits exactly that set.
3. `assess()` decides, and fails closed.

Current result, run locally against `uv.lock`: **96 third-party packages audited, 0 known
vulnerabilities.** No remediation was needed and none was applied — no pin moved, no version bumped,
nothing suppressed. The only dependency change in this slice is adding the scanner itself.

## Four decisions worth reading before changing this

**The lock is the input, not the environment.** Auditing installed packages would audit whatever the
runner happened to resolve, so a lock pinning a vulnerable version could pass on a machine with
something else installed — green about the wrong artifact. `--frozen` makes `uv.lock` authoritative
and forbids re-resolution.

**Nothing is installed and no package code runs.** `--no-deps` means pip-audit reads the pinned set as
given instead of resolving it; `--disable-pip` keeps pip out of the process. Resolving a dependency can
mean downloading an sdist and executing its `setup.py` — arbitrary third-party code inside a CI job.
These two flags are the Python equivalent of the `--ignore-scripts` the pnpm install already uses, and
the untrusted-workflow posture is otherwise untouched: the job still checks out with
`persist-credentials: false` and references no repository secret.

**Workspace members are excluded, and only they.** `--no-emit-workspace` drops this repository's own
packages, which are editable, carry no hashes and appear in no advisory database. Everything
third-party stays. Narrow on purpose: excluding anything else would be a blanket suppression wearing a
flag's clothing.

**There is no ignore list.** Not an empty one — none. A suppression mechanism is the thing that gets
used at four in the morning. When an advisory genuinely does not apply, the honest form is a recorded
decision in the lock that a reader can see, arriving with the advisory id, the reason and a date, as
code review.

## Provenance of the scanner

`pip-audit` is a locked dev dependency, so `uv.lock` fixes its version **and its artifact hashes** like
everything else it audits — a stronger claim than a version string in a workflow file, which pins a
number but not the bytes. A scanner resolved fresh on every run is one whose behaviour can change
without a commit.

## Why the gate cannot silently go green

Each of these is a way a dependency gate dies quietly, and each has a test that fails if it happens.
All are mutation-checked: the defect is reinstated and the named test fails.

| If this happened | Caught by |
|---|---|
| The step is deleted or stops invoking the scan | `the_security_job_runs_the_python_dependency_audit` |
| `continue-on-error`, `\|\| true`, `set +e` | `the_audit_step_cannot_fail_open` |
| `--frozen` dropped, so the export re-resolves | `keeps_the_flags_that_make_it_locked_and_script_free` |
| `--no-deps` or `--disable-pip` dropped, so third-party setup code executes | same |
| `--strict` dropped, so an unreadable package is skipped and still exits zero | same |
| An advisory stops failing the gate | `a_single_advisory_fails_the_gate`, `every_advisory_is_reported` |
| The export is truncated or empty | `an_almost_empty_audit_fails_as_wrong_input` |
| A requirement becomes a range | `an_unpinned_requirement_fails` |
| Hashes disappear from the export | `requirements_without_hashes_fail_as_missing_provenance` |
| pip-audit fails to run and emits no report | `an_unparseable_report_fails_rather_than_reading_as_clean` |
| A suppression flag is introduced | `there_is_no_suppression_mechanism`, `a_suppression_reachable_at_runtime_is_caught` |
| Prose naming a flag breaks the build instead | `explanatory_prose_naming_a_flag_is_not_a_suppression` |
| The workflow keeps its own copy of the rule | `the_workflow_guard_uses_the_same_detector_rather_than_a_substring_search` |
| The scanner is unpinned | `the_scanner_itself_is_pinned_in_the_lock` |

`assess()` is pure, so those decisions are tested without a network, a subprocess, or an advisory
database that changes under the suite. A clean audit of a healthy lock exercises none of them, which is
why the suite is mostly inputs that must fail — and why it includes a control asserting a healthy set
*passes*, without which every other test would also pass against a gate that refused everything.

The workflow self-check in `ci.yml` asserts the structural half from inside CI, so the gate is
checked both ways. Two versions of it were wrong about the same thing — searching raw text — and both
are worth recording.

**It matched itself.** The guard's own source names the script and every pattern it searches for, so
it failed on its own text. That was the second time this project made that mistake with a check over
raw workflow text; it now excludes the one step that parses YAML.

**It searched the script's prose for suppression flags.** Independent review reported this as
CI-breaking. On the reviewed head it was not: the docstring said "there is no ignore list" without
using the exact spellings, so the guard passed. But the fragility was real and one clarifying comment
away — in a codebase where every guard explains itself, writing `never pass --ignore-vuln` in a comment
would have turned the security job red, and the obvious fix would have looked like deleting the check.

Suppression detection is now `suppression_in_code`, which parses the module, drops docstrings and
re-renders it with `ast.unparse` (which discards comments). What remains is executable code: a flag
passed to a subprocess, an element of a command list, a keyword argument, or a constant holding the
flag all fail; the same flag named in prose does not. The workflow guard imports that one function
rather than keeping a second copy, because two copies with different rules is what produced the bug —
the inline guard searched raw text while the pytest test stripped comments, so they disagreed and only
one of them ran in CI.

## Limitations — read these

- **Platform-specific by construction.** Eleven locked packages carry environment markers, so the
  audited set is the one that resolves on the CI runner (Linux). A dependency that only installs on
  Windows or macOS is not audited. Auditing every marker combination would mean replicating uv's
  resolver, and the product's own runners are the platforms that matter — but this is a real gap, not
  an oversight.
- **The floor is a floor, not an equality.** `MINIMUM_AUDITED_PACKAGES = 50` catches a truncated or
  empty export; it would not catch an export that silently lost ten packages. Exactness is not
  available without evaluating markers as uv does.
- **Advisory coverage is PyPI's, via pip-audit's default sources.** A vulnerability with no published
  advisory is invisible here, as it is to every scanner.
- **No severity grading.** Every advisory fails, at every severity. pip-audit reports no severity for
  many entries, so a threshold would silently drop exactly the ones it could not grade — but it does
  mean a low-severity advisory blocks the build until somebody acts, and the only honest actions are a
  fix or a recorded decision.
- **`uv.lock` is not verified against the remote index here.** The gate trusts the lock's hashes to
  describe the artifacts; it does not re-download them to confirm. `uv sync --frozen` earlier in the
  job is what checks that.
