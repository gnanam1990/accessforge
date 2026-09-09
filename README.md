# AccessForge

**Status: implementation in progress. No working application exists in this repository yet.**

AccessForge is intended to help engineers reproduce a web accessibility barrier with a real
screen reader, prepare a constrained source repair, independently rerun the journey, and
review or export exactly what was established. It is not an accessibility overlay, a generic
chatbot, or a legal-compliance certificate.

## What is actually here right now

| Path | Contents |
|---|---|
| `specs/accessforge/` | The complete product specification package (PRD, TDD, CONTRACTS, TEST-PLAN, UI-UX, SECURITY-PRIVACY, RELEASE-CHECKLIST, SOURCES, and 30 numbered build prompts). Specification only. |
| `docs/` | Implementation-owned records produced as modules land: capability evidence, ADRs, module handoffs, and the delivery plan. |

Nothing in `specs/` asserts that code, tests, integrations, or releases exist. Claims about
this repository's actual state live in `docs/delivery/STATUS.md` once module 00 lands.

## Release scope vocabulary

- **E0** — one authorized application, one pinned macOS/browser/actual VoiceOver profile, one
  form-error recovery journey, with real navigation, an actual patch, an independent rerun,
  and human review.
- **R1** — the complete web product, adding actual Windows/NVDA support and production operations.
- **R2+** — mobile, PDF remediation, native desktop apps, managed multi-tenant desktop fleets.
  Out of scope here.

## Build workflow

Execution follows `specs/accessforge/MASTER-BUILD-AND-MERGE.md`: numbered modules in dependency
order, each delivered as its own reviewed pull request with evidence, merged into `main`, and
verified afterward. Module 00 (capability gate) runs first and establishes which real boundaries
are available before anything is promised.
