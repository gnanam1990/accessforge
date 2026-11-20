# AccessForge Tester Desk · Session handoff

Initial state: specification supplied. No target work has been verified.

- Target repository / branch / tested SHA: accessforge-tester-desk workspace / `devin/accessforge-tester-desk` / intake HEAD `3f529c1` (pack + upstream AccessForge baseline, working tree clean).
- Current module and owner: M01 assigned to this session (Devin, SWE-2 High local).
- Completed behavior and redacted evidence path: none yet — intake only.
- Working tree or branch requiring transfer: all work stays on `devin/accessforge-tester-desk` in this checkout.
- Commands, exit codes and tested environment:
  - `uv sync --frozen` with `UV_CACHE_DIR=$PWD/.uv-cache` (the default `~/.cache/uv` is outside the exec sandbox) — exit 0.
  - `pnpm install --frozen-lockfile --package-import-method=copy --filter @accessforge/web` — exit 0. **Limitation:** full-workspace `pnpm install` fails inside the exec sandbox on `xmlbuilder@15.1.1`, which ships `.vscode/launch.json`; `.vscode` paths are deny-listed for writes in this sandbox, so the file cannot be materialized. `xmlbuilder` is only a transitive dependency of `@guidepup/guidepup` in `packages/at-adapters/voiceover`. Recorded limitation, not patched: per owner direction, no sandbox-only dependency patches are committed.
  - `pnpm --filter @accessforge/web test` — 29 files / 486 tests, all passed.
  - `pnpm --filter @accessforge/web typecheck` — fails at `vite.config.ts(17,24)` (`Cannot find name 'process'`): `@types/node` is not installed under the filtered install; it is a devDependency of other workspace packages. Will re-check once the needed packages are installed; screen work can proceed independently.
  - PostgreSQL: SysV `shmget` is denied inside the exec sandbox, so neither `initdb` nor a workspace-local cluster can start here. An already-running Homebrew PostgreSQL 17 is reachable over TCP `localhost:5432`. Per owner direction the existing `accessforge`/`accessforge_test` databases and roles were NOT touched; a dedicated namespace was provisioned: role `af_td` (NOSUPERUSER NOBYPASSRLS), databases `af_td_dev` and `af_td_test` (owner `af_td`).
  - `uv run python scripts/migrate.py --database-url postgresql://af_td:af_td_local@localhost:5432/af_td_dev` — applied 0001–0074, exit 0.
  - `.env.test` (gitignored): `TEST_DATABASE_URL=postgresql://af_td:af_td_local@localhost:5432/af_td_test`, `BACKUP_DATABASE_URL=postgresql://kratos@localhost:5432/af_td_test`.
  - `.env` (gitignored): dev DB `af_td_dev`, local environment, evidence endpoint placeholder `http://localhost:9000` (no local object store running — evidence-store-backed routes will report the dependency; tester-desk attachments use a dedicated table instead).
  - `uv run pytest tests/integration/test_membership_lifecycle.py -q` — 3 passed (baseline sanity).
  - `ACCESSFORGE_IDENTITY_PROVIDER=local-development` is intentionally NOT in `.env`: the test harness loads `.env` and `ApiSettings` refuses that provider outside `environment=local`. It is exported by `.env.local.sh` for manual runs only.
- External dependencies and decisions: see KITE-AND-PROVIDERS.md. No live payout provider, consenting human tester, or agency-controlled test URL is authorized for this run; the compensation adapter is built behind an explicit interface so a real provider can be attached, and the finance surface reports the integration state honestly.
- First next action: implement migration `0075_tester_desk_commissions.sql`, domain types for briefs/fees/rubrics, persistence module `tester_desk`, and the commission authoring + publish routes; then prove T-001.
- Next agent must reproduce: `source .env.local.sh && uv run pytest tests/unit/test_tester_desk*.py tests/integration/test_tester_desk*.py -q`.
