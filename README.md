# AccessForge

**Status: implementation in progress. The AccessForge product itself does not exist yet.**

What runs today is the workspace foundation and a reference application used as a test target.
No screen reader has ever been driven by this repository, and no accessibility claim is made.

AccessForge is intended to help engineers reproduce a web accessibility barrier with a real
screen reader, prepare a constrained source repair, independently rerun the journey, and
review or export exactly what was established. It is not an accessibility overlay, a generic
chatbot, or a legal-compliance certificate.

## What is actually here right now

| Path | Contents |
|---|---|
| `specs/accessforge/` | The complete product specification package (PRD, TDD, CONTRACTS, TEST-PLAN, UI-UX, SECURITY-PRIVACY, RELEASE-CHECKLIST, SOURCES, and 30 numbered build prompts). Specification only. |
| `docs/` | Implementation-owned records produced as modules land: capability evidence, ADRs, module handoffs, and the delivery plan. |
| `apps/` | `api` (control-plane configuration and health), `web` and `desktop-runner` build targets. Runner behaviour is module 07/08; the UI is module 21. |
| `fixtures/reference-app/` | A genuinely working local service-request application with PostgreSQL persistence, used as the authorized target under test. |
| `tests/` | Unit and integration suites. Integration runs against a real PostgreSQL server and fails rather than skips when one is absent. |
| `packages/`, `infra/` | Boundary markers naming the owning module. No implementation yet. |

Nothing in `specs/` asserts that code, tests, integrations, or releases exist. Claims about
this repository's actual state live in `docs/delivery/STATUS.md` once module 00 lands.

## Release scope vocabulary

- **E0** — one authorized application, one pinned macOS/browser/actual VoiceOver profile, one
  form-error recovery journey, with real navigation, an actual patch, an independent rerun,
  and human review.
- **R1** — the complete web product, adding actual Windows/NVDA support and production operations.
- **R2+** — mobile, PDF remediation, native desktop apps, managed multi-tenant desktop fleets.
  Out of scope here.

## Getting started

See [docs/development/VERIFICATION.md](docs/development/VERIFICATION.md) for install, database
setup, the full verification ladder, and how to run and inspect the services.

## Build workflow

Execution follows `specs/accessforge/MASTER-BUILD-AND-MERGE.md`: numbered modules in dependency
order, each delivered as its own reviewed pull request with evidence, merged into `main`, and
verified afterward. Module 00 (capability gate) runs first and establishes which real boundaries
are available before anything is promised.
