# AccessForge Tester Desk · Session handoff

State at `28c9aa3`: all eight modules implemented and verified against the
dedicated local database. The full AF-21 pilot journey runs end-to-end through
real API entry points; the web surfaces are live in the app shell and covered
by the web test suite. Nothing is pushed, no PR is open, no live external
integration was exercised — the exact gaps are listed below.

- Target repository / branch / tested SHA: accessforge-tester-desk workspace /
  `devin/accessforge-tester-desk` / `28c9aa3` (tests ran on the working tree
  that produced this commit).

## What exists

Backend (FastAPI + psycopg + RLS), all under the `/workspaces/{id}/tester-desk`
route prefix in `apps/api/src/accessforge_api/routes/tester_desk.py`:

- M01 commissions: clients, consent terms, versioned commission authoring,
  publish-pinned brief revisions (`td_brief_revision` immutable by trigger,
  canonical SHA-256 digest). Migration `0075`.
- M02 eligibility: restricted tester enrollment, capability matching, conflict
  checks, consent acceptance, scoped offer list with expiry, `claim_offer`
  under `FOR UPDATE` so raced claims yield exactly one winner; credentials
  readable only by the winner. Migration `0076`.
- M03 evidence capture: `HUMAN_MANUAL`-only observations, per-step outcomes,
  draft upsert/recovery, attachment allowlist (kind -> content types, 8 MiB
  cap), SHA-256 digest at write re-verified at read, disallowed recording
  kinds refused before any artifact exists. Migration `0077`.
- M04 submission + review: sealed `td_submission` (immutable by trigger),
  completeness vs rubric, task outcome derived separately from deliverable
  decision, digest-bound reviewer decision, justified revision requests,
  obsolete-digest -> 409. Migration `0078`.
- M05 compensation: payable created exactly once on ACCEPTED (assignment
  unique-keyed), `PayoutAdapter` interface with `LocalLedgerAdapter` stand-in,
  dispatch marks `DISPATCHED`/`UNKNOWN` on uncertain submit, reconcile by
  provider reference settles once; retry never re-dispatches. Migration `0079`.
- M06 client reporting: digest-pinned `td_report_revision` per assignment,
  consent-scoped evidence manifest, client grants, cross-client isolation,
  retention purge that drops bytes but keeps manifests. Migrations `0080`.
- M07 retest + comparison: follow-up commission links predecessor by defect
  key, comparison matches task/environment dimensions, `environment.*` caveats
  on AT/OS/browser drift, fresh retest starts `UNTESTED` — a prior pass is
  never inherited.
- M08 pilot + web surfaces: full AF-21 scene in
  `tests/integration/test_tester_desk_pilot.py`; four web screens wired into
  the app shell — agency console (`/tester-desk`), tester inbox
  (`/tester-desk/tester`), capture (`/tester-desk/tester/assignments/:id`),
  client reports (`/tester-desk/client`), API client
  `apps/web/src/api/testerDesk.ts`.

## Verification (commands, exit codes)

- `uv run pytest tests/integration/ -k tester_desk` — 32 passed, 1468
  deselected, exit 0 (all module suites incl. the AF-21 pilot).
- `uv run mypy apps/api/src` — no issues in 42 source files, exit 0 (CI parity).
- `uv run ruff check` on all new/edited Python files — all checks passed.
- `cd apps/web && npx vitest run` — 30 files / 488 tests passed, exit 0.
- `cd apps/web && npx tsc --noEmit` — new tester-desk files clean; remaining
  errors are the pre-existing `@types/node` gap from the filtered install
  (`node:fs`, `node:path`, `process`, `__dirname` in unrelated tooling files).
- `uv run pytest tests/unit -q` — 2148 passed, 13 failed: all 13 are
  sandbox-only `tempfile.mkdtemp` denials in
  `test_navigator_native_transport.py` (`/tmp` writes blocked); identical on
  the intake tree, unrelated to this work.
- Migrations `0075`–`0080` applied to `af_td_dev`; test suite runs against
  `af_td_test`.

## Dedicated environment

- Role `af_td` (NOSUPERUSER NOBYPASSRLS); databases `af_td_dev`,
  `af_td_test` on the running Homebrew PostgreSQL 17. Existing
  `accessforge`/`accessforge_test` databases and roles were never touched.
- `.env` / `.env.test` are gitignored and point only at the `af_td_*` URLs.
- A workspace-local Postgres cluster is not possible in this sandbox
  (`shmget` denied); the running server is used as a host with a unique
  namespace instead.

## External dependencies — exact missing proof

- Live payout provider: not authorized in this run. `LocalLedgerAdapter`
  stands in behind `PayoutAdapter`; missing proof = a signed provider route,
  seller authority, and a real dispatch/ack exchange.
- Independent human tester: no consenting tester ran a real
  VoiceOver+Safari/keyboard-only session; capability claims and observations
  in tests are fixture data only.
- Agency-controlled test URL: `AF21_BRIEF.targetUrl` is a fixture; no real
  checkout build exists.
- Real-browser E2E: UI is proven through jsdom + Testing Library against the
  real fetch client, not a driven browser at desktop/tablet/phone widths.
- Full `pnpm install`: blocked by sandbox deny on `.vscode` paths inside
  `xmlbuilder@15.1.1` (pulled only by the guidepup voiceover AT-adapter).
  Filtered `pnpm --filter @accessforge/web ...` works; no temporary patch
  was committed.

## Next agent

1. Reproduce: `uv run pytest tests/integration/ -k tester_desk` (32 pass) and
   `cd apps/web && npx vitest run` (488 pass) against `af_td_test`.
2. To close the pilot for real: wire a payout provider implementing
   `PayoutAdapter`, run the AF-21 scene with a consenting tester on
   VoiceOver+Safari against an agency-controlled URL, and add a browser-driven
   E2E once a display/browser session is available.
