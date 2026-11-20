# AccessForge Tester Desk · Session handoff

State at `25b4132`: M01 and M02 implemented and verified against the dedicated
local database. M03 in progress next.

- Target repository / branch / tested SHA: accessforge-tester-desk workspace /
  `devin/accessforge-tester-desk` / `25b4132` (tests ran on the working tree that
  produced this commit).
- Completed behavior and redacted evidence path:
  - M01 commissions + immutable brief revisions: `packages/persistence/.../0075_tester_desk_commissions.sql`,
    `packages/domain/.../tester_desk.py`, `packages/persistence/.../tester_desk.py`,
    `apps/api/.../routes/tester_desk.py` (clients, consent terms, commissions).
    Published brief revisions are immutable by trigger; T-001 (publish `r18`,
    5-step checkout, reopen exact revision) and T-002 (fee edit after
    acceptance still reads `20.00` from the pinned revision) pass.
  - M02 eligibility + consent + atomic claim: `0076_tester_desk_enrollment_assignment.sql`,
    enrollment/capability/conflict routes, scoped offer list with expiry,
    `claim_offer` using `FOR UPDATE` row lock so concurrent eligible testers
    produce exactly one winner; only the winner reads the access credentials.
    T-003 (Safari + VoiceOver consenting tester claims) and T-004 (raced
    claims → single winner) pass.
  - Integration evidence: `tests/integration/test_tester_desk_commissions.py`
    (5 tests), `tests/integration/test_tester_desk_enrollment.py` (6 tests),
    shared fixture `tests/integration/conftest.py`.
- Commands, exit codes and tested environment:
  - `uv run pytest tests/integration/test_tester_desk_commissions.py tests/integration/test_tester_desk_enrollment.py -q` — 11 passed, exit 0.
  - `uv run pytest tests/unit -q` — 2148 passed, 13 failed, exit 1. All 13
    failures are in `test_navigator_native_transport.py` and are sandbox-only:
    `tempfile.mkdtemp` is denied writes to `/tmp` in this exec sandbox
    (`PermissionError: [Errno 1] Operation not permitted: '/tmp/afn-*'`).
    Unrelated to tester-desk code; they fail identically on the intake tree.
  - `uv run ruff check` on all new/edited files — all checks passed.
  - `uv run python scripts/migrate.py --database-url postgresql://af_td:af_td_local@localhost:5432/af_td_dev` — applied `0075`, `0076`, exit 0.
  - Dedicated DB namespace (existing `accessforge`/`accessforge_test` untouched):
    role `af_td` (NOSUPERUSER NOBYPASSRLS — RLS checks are meaningful),
    DBs `af_td_dev`, `af_td_test`.
- Working tree or branch requiring transfer: everything is committed on
  `devin/accessforge-tester-desk`; `build-progress/` updated in the next commit.
- External dependencies and decisions:
  - No live payout provider authorized — compensation will use a local ledger
    adapter behind an explicit interface (M05), exact missing proof: signed
    provider route + seller authority.
  - No consenting independent human tester or real VoiceOver/Safari run —
    capability claims in tests are fixture data only; M08 must report this
    honestly.
  - No agency-controlled test URL — `AF21_BRIEF.targetUrl` is a fixture.
  - pnpm full-workspace install blocked by sandbox deny on `.vscode` paths
    inside `xmlbuilder@15.1.1` (voiceover AT-adapter only). Filtered install
    of `@accessforge/web` works; `pnpm --filter @accessforge/web test` 486
    pass. `typecheck` fails on missing `@types/node` under filtered install
    (pre-existing tooling gap, unrelated to product code).
- First next action: M03 evidence capture — observation drafts with autosave
  and reload recovery, per-step outcomes, permitted attachment types with
  content digests, refusal of disallowed recording types, and the
  screen-reader/keyboard-usable capture UI surface.
- Next agent must reproduce:
  `uv run pytest tests/integration/test_tester_desk_commissions.py tests/integration/test_tester_desk_enrollment.py -q` (11 pass).
