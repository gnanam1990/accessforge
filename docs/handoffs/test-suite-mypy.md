# P6 — Strict type-checking for the test suite

Baseline: main `49747cbe8cf79dbc416b6028224dd4bdcc641ba6` (PR #35).
Branch: `feat/test-suite-mypy`. Local validation passed; GitHub CI and merge are pending.

## Scope

The original inventory said 139 errors. A fresh strict check of the tests reported 234 errors in
29 files. The CI type-check now includes `tests/`, plus the previously omitted Python client
package, alongside all existing application, domain, persistence and operator-script targets.
`strict = true` remains unchanged, with no new mypy overrides or excluded tests.

## What changed

- Authorization and configuration helpers have explicit typed arguments. Every negative case still
  reaches the real boundary; grant-containment cases use typed calls rather than unchecked casts.
- DB rows and cookie values are asserted present before indexing or serialization checks. A missing
  fixture now fails as a missing fixture, rather than being hidden behind a type assertion.
- TestClient helpers return the locked Starlette stack's actual `httpx2.Response` type; the public
  Python client's independent `httpx` transport is unchanged.
- Fault stores implement the complete artifact-store protocol. Reads, uploads and existence probes
  in a purge raise assertions; deletion outages and interrupted drains retain their original behavior.
- Shared JSON vectors use explicit shapes; enums, callbacks and connection rows are annotated.
  CLI tests no longer overwrite their manifest-builder callable with the digest it returned.
- CI-configuration tests require all Python workspace members plus tests/scripts, reject a skipped
  or fail-open type-check step, and prohibit new type-check exemptions.

## Verification

- Strict mypy: 197 source files, no issues.
- Ruff lint and formatting: clean.
- Authority and CI-configuration tests: 52 passed.
- Full Python runtime suite: `uv run pytest -q --tb=short` — 1,863 passed, zero failures or skips,
  58 upstream deprecation warnings, including real PostgreSQL/MinIO and backup/restore tests.
- `pnpm typecheck`, `pnpm build`, `pnpm test`: all passed.
- Live OpenAPI and generated contract binding drift checks: passed.
- Five in-memory mutations were rejected by the CI guard tests: omitting tests, scripts or the
  Python client, disabling strict mode, and excluding tests.

## Review

The first full runtime pass caught one introduced incomplete rename: the idempotency-conflict
test still put its manifest-builder callable into the second JSON body. That reference is corrected;
the second complete run above includes the negative conflict case and passes.

The review traced changed helpers to their production interfaces, compared every changed negative
case against the baseline, checked DB/cookie preconditions and fault-store semantics, and inspected
CI target coverage. No remaining evidence-backed defects were found in this scope. No new dependency,
provider or runtime protocol was introduced, and neither the lock nor the specification pack changed.
This is an internal maintainer review, not an independent security audit.

This is a verification-infrastructure change, not evidence of actual VoiceOver/NVDA execution,
model-provider invocation, a sandboxed candidate build, or production readiness. Existing deliberately
untyped external JSON boundaries and pre-existing targeted type ignores are not all eliminated.
