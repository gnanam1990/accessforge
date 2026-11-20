# AccessForge Tester Desk · Session handoff

State at `5435920` plus the browser-proof slice: all eight modules implemented
and verified against the dedicated local database, and the documented browser
journey now runs end-to-end in a real Chromium against the real FastAPI + Vite
stack — 38/38 named checks pass. Nothing is pushed, no PR is open, no live
external integration was exercised — the exact gaps are listed below.

- Target repository / branch: accessforge-tester-desk workspace /
  `devin/accessforge-tester-desk`
- Tested SHA: `5435920` (product code); the browser-proof slice on top was
  verified on the working tree this commit records.
- Browser proof: `node tools/tester-desk-browser/check.mjs` — see
  `build-progress/accessforge-tester-desk/BROWSER.md` for setup, env vars and
  the redacted evidence record.

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

- M09 browser proof (this slice): `tools/tester-desk-browser/` holds a
  portable check — `provision.py` builds a disposable AF-21 scene through the
  real HTTP entry points; `check.mjs` boots API+Vite on ephemeral ports and
  drives Chromium through sign-in → consent-gated claim → keyboard capture →
  evidence attach → draft-reload persistence → sealed submit → reviewer
  decision → payable + client report → permission refusals → 320px + desktop
  Tab audits. Five real product gaps found by the browser run were fixed in
  the product, not patched around:

  - Non-member testers / client viewers could never reach their surfaces:
    `GET /v1/session` only listed `workspace_membership` rows. Now unions
    active `td_tester` (role TESTER) and `td_client_viewer` (role CLIENT)
    rows; member role wins on overlap. RLS gained SELECT-only own-row
    policies (migration `0081`) — writes stay workspace-scoped.
  - Workspace index sent every role to overview and the nav showed member
    routes a participant cannot answer. Landing now routes TESTER→inbox,
    CLIENT→reports, members→overview; navigation filters by role.
  - The offer list omitted the consent body a tester must attest to — now
    pinned onto each offer item.
  - The capture screen had no attachment control, so a rubric requiring
    `ACTION_TRACE` could never be satisfied through the UI — an upload
    section + digest manifest table now exists.
  - 320px overflow from grid/flex `min-width:auto` — children now shrink and
    tables clip inside `.af-table-scroll`.

## Verification (commands, exit codes)

- `node tools/tester-desk-browser/check.mjs` — **38/38 checks passed**,
  exit 0 (sandbox invocation adds
  `AF_BROWSER_ARGS="--single-process --no-sandbox"`). Redacted record:
  `tools/tester-desk-browser/evidence/2026-11-20T07-34-08/`.
- `uv run pytest tests/integration/ -k tester_desk` — 37 passed, 1468
  deselected, exit 0 (all module suites incl. the AF-21 pilot and the new
  session-scope/RLS regressions).
- `uv run mypy apps/api/src` — no issues in 42 source files, exit 0 (CI parity).
- `uv run ruff check` on all new/edited Python files — all checks passed.
- `cd apps/web && npx vitest run --maxWorkers=2` — 30 files / 488 tests
  passed, exit 0. Bounded workers are the reliable host command: the
  unbounded run once produced a single failure in unrelated
  `src/screens/diagnosis.test.tsx` (async finding navigation) that passes
  in isolation and under bounded workers — a load-sensitive baseline flake,
  not a tester-desk regression.
- `cd apps/web && npx tsc --noEmit` — new tester-desk files clean; remaining
  errors are the pre-existing `@types/node` gap from the filtered install
  (`node:fs`, `node:path`, `process`, `__dirname` in unrelated tooling files).
- `uv run pytest tests/unit -q` — 2148 passed, 13 failed: all 13 are
  sandbox-only `tempfile.mkdtemp` denials in
  `test_navigator_native_transport.py` (`/tmp` writes blocked); identical on
  the intake tree, unrelated to this work. Host reported 2161 unit + 32
  tester-desk integration tests passing at `23f1d1c` with the full
  workspace + desktop-runner dependencies installed.
- Migrations `0075`–`0081` applied to `af_td_dev`; test suite runs against
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
- Full `pnpm install`: blocked by sandbox deny on `.vscode` paths inside
  `xmlbuilder@15.1.1` (pulled only by the guidepup voiceover AT-adapter).
  Filtered `pnpm --filter @accessforge/web ...` works; no temporary patch
  was committed.

## Next agent

1. Reproduce: `uv run pytest tests/integration/ -k tester_desk` (37 pass),
   `cd apps/web && npx vitest run --maxWorkers=2` (488 pass) against
   `af_td_test`, and `node tools/tester-desk-browser/check.mjs` (38 checks)
   per `BROWSER.md`.
2. To close the pilot for real: wire a payout provider implementing
   `PayoutAdapter` and run the AF-21 scene with a consenting tester on
   VoiceOver+Safari against an agency-controlled URL.
