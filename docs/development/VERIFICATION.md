# Verification command manifest

Every command here exists, has been executed, and fails when its prerequisites are missing.
A command that would pass with its dependency absent is not listed as proof of that dependency.

## Prerequisites

- Python 3.13 with [uv](https://docs.astral.sh/uv/)
- Node 22 with pnpm 11
- A running PostgreSQL 17 server
- Copy `.env.example` → `.env`, `.env.refapp.example` → `.env.refapp`, `.env.test.example` → `.env.test`,
  and replace every placeholder. Startup and configuration validation reject example values.

One file per settings consumer is deliberate: each settings class uses `extra="forbid"`, so a typo
in a variable name fails startup instead of silently leaving a default in place. A single shared
file would make every other prefix look like an unknown key.

## Install

```bash
uv sync --frozen      # fails if uv.lock does not match pyproject.toml
pnpm install --frozen-lockfile
```

## First-time local database

```bash
createuser accessforge --login --pwprompt
createdb accessforge      --owner accessforge
createdb accessforge_refapp --owner accessforge
createdb accessforge_test   --owner accessforge
```

The role is intentionally **not** a superuser, and the databases are dedicated. Nothing here
reuses an existing database.

**This is load-bearing, not hygiene.** Tenant isolation is enforced by row-level security, and a
PostgreSQL superuser — or any role with `BYPASSRLS` — ignores every policy including `FORCE`. Running
the suite as a superuser would execute every isolation assertion against no isolation at all. CI found
this the hard way: the official postgres image creates `POSTGRES_USER` as a superuser, so the first
run of these tests failed sixteen assertions at once.

`assert_row_level_security_enforced()` now checks this before the isolation suites run and fails with
one sentence naming the cause:

```bash
uv run python -c "
import os
from accessforge_persistence import assert_row_level_security_enforced
assert_row_level_security_enforced(os.environ['TEST_DATABASE_URL'])
"
```

## Database migrations

```bash
uv run python -c "from accessforge_persistence import migrate; print(migrate('<database-url>'))"
```

Each migration runs in its own transaction and is recorded in the same transaction, so a failure
leaves neither a half-applied schema nor a false record of success. The integration fixtures call
`migrate()` themselves, so `pytest tests/integration` needs no separate migration step.

## Verification ladder

| Layer | Command | What it actually proves |
|---|---|---|
| Format | `uv run ruff format --check .` | Formatting is normalized |
| Lint | `uv run ruff check .` | Lint rules including security (`S`) and import boundaries (`TID`) |
| Types | `uv run mypy apps/api/src fixtures/reference-app/src packages/domain/src packages/contracts/python/src` | Strict typing across all four Python packages |
| Unit | `uv run pytest tests/unit -q` | Validation, fail-closed configuration, redaction, fixture-variant integrity, outcome precedence, reducers, authority, property tests |
| Contract | `uv run pytest tests/contract -q` | Schema validation, RFC8785 canonicalization, and Python/TypeScript digest agreement |
| Binding drift | `uv run python scripts/generate_contract_bindings.py --check` | Generated bindings still match the authoritative schemas |
| RLS precondition | `assert_row_level_security_enforced(TEST_DATABASE_URL)` | The test role cannot bypass row-level security, so the isolation suite means something |
| Integration | `uv run pytest tests/integration -q` | Real PostgreSQL: journey, durability, readiness, row-level tenant isolation, session/CSRF/enrollment boundaries |
| Node types | `pnpm -r --if-present typecheck` | TypeScript strict mode |
| Node build | `pnpm -r --if-present build` | Both TS packages compile |
| Node tests | `pnpm -r --if-present test` | Runner reports non-implementation rather than false success; TypeScript canonicalization matches the shared vectors. Builds first, since the tests import from `dist/` |
| Everything | `uv run pytest tests -q && pnpm -r --if-present test` | Full local suite |

`tests/integration` **fails** rather than skips when `TEST_DATABASE_URL` is absent
(`test_integration_suite_is_actually_configured`), so a missing database cannot present itself as
a green run.

## Running the services

```bash
uv run python -m reference_app     # http://127.0.0.1:8081  (loopback binding is enforced)
uv run python -m accessforge_api   # http://127.0.0.1:8080
```

Shutdown: stop those processes only. Do not stop the PostgreSQL server; it is not owned by this
project.

## Health inspection

```bash
curl -s http://127.0.0.1:8081/health/live     # process is running
curl -s http://127.0.0.1:8081/health/ready    # dependencies are actually reachable
curl -s http://127.0.0.1:8080/health/ready    # 503 until an S3-compatible store is running
curl -s http://127.0.0.1:8080/diagnostics     # configuration with every credential redacted
```

Liveness and readiness answer different questions. A 200 from `/health/live` says only that the
process responds. With no object store running, `/health/ready` on the API returns **503** and
names `evidence-store` — that is the intended behaviour, not a defect to be smoothed over.

## Exercising the reference application

```bash
SETUP=$(grep '^REFAPP_SETUP_TOKEN=' .env.refapp | cut -d= -f2)
OBS=$(grep  '^REFAPP_OBSERVER_TOKEN=' .env.refapp | cut -d= -f2)

# Create a fresh fixture instance (setup identity)
curl -s -X POST 'http://127.0.0.1:8081/api/_test/fixtures?variant=inaccessible' \
     -H "x-setup-token: $SETUP"

# Read durable state (observer identity only — never give this token to a navigator)
curl -s "http://127.0.0.1:8081/api/_test/receipt/<nonce>" -H "x-observer-token: $OBS"

# Reset this application's own tables (setup identity)
curl -s -X POST 'http://127.0.0.1:8081/api/_test/reset' -H "x-setup-token: $SETUP"
```

## Regenerating contract bindings

```bash
uv run python scripts/generate_contract_bindings.py           # write
uv run python scripts/generate_contract_bindings.py --check   # verify, non-zero if stale
```

The JSON Schemas in `packages/contracts/schemas/` are authoritative. The generated files are
committed only so drift is detectable; editing one by hand is pointless, because the next check
overwrites the intent and fails.

The cross-language differential test requires the TypeScript build:

```bash
pnpm --filter @accessforge/contracts build
uv run pytest tests/contract -q
```

It fails rather than skips when that build output is missing. The Node test scripts build
themselves, so `pnpm -r test` works from a clean checkout without a separate build step.

## Mutation checks for high-risk guards

Run in a scratch copy; never commit mutated code. Each guard, when broken, must make specific
tests fail:

| Mutation | Expected result |
|---|---|
| Authorization checks always allow | integration identity tests fail |
| Email validation removed | validation and journey tests fail |
| Readiness always reports ready | readiness-failure test fails |
| Inaccessible variant rendered accessible | fixture-variant tests fail |
| Loopback binding guard removed | configuration tests fail |
| CSRF verification disabled | 8 auth tests fail |
| Session or membership revocation not checked | 3 and 1 auth tests fail |
| Enrollment redemption not single-use | 1 auth test fails |
| `FORCE` removed from row-level security | both workspaces leak (asserted by a self-test) |
| Canonical key order changed to code point | UTF-16 ordering test fails |
| Cancelled outcome keyed on status rather than execution | reducer regression and property tests fail |

Verified on 2026-09-09; results are in `docs/handoffs/01.md`.

## Not yet available

| Layer | Status |
|---|---|
| **Actual VoiceOver** | **BLOCKED** — not configured on this host; module 08 owns the runner. See `docs/capabilities.md` §5.1 |
| **Actual NVDA** | **BLOCKED** — no Windows host; module 09 |
| Object store integration | Configuration and readiness only; module 10 owns the real contract |
| UI | Module 21 onward. `apps/web` is a build target with no interface |
| Release operations | Module 27 onward |

## Evidence storage

Run outputs go to the git-ignored `.evidence/` directory and are referenced by path from pull
requests and handoffs. They are never committed, and a passing run is never recorded by adding a
"tests passed" file to the tree.
