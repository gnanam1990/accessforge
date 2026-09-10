# AccessForge threat model

**Produced by:** module 26. **Bound to:** the code in this repository at the commit that carries
this file. Every "enforced by" row names a file, a table constraint or a policy that exists; every
"not enforced" row says so.

This is a source-bound threat model, not a security posture statement. Where a control is claimed,
a test is named. Where a control is absent, the row says absent rather than planned. A document
that said "isolated" would be a hypothesis; the probes in
`tests/integration/test_boundary_probes.py` are what makes any of this a finding.

---

## 1. What is being protected, and from whom

| Asset | Why it is worth attacking |
|---|---|
| Customer source at an exact revision | It is the customer's code |
| Test credentials and credential *references* | They reach an environment somebody authorized |
| Reader observations and transcripts | They contain whatever was on a real screen, including what a person typed |
| Desktop input authority | Whoever holds a lease can drive a real computer |
| Evidence and its chain | A false PASS is the product's one unforgivable output |
| Approval records | An approval is permission to change somebody's software |
| Session and enrollment credentials | Either is account or desktop takeover |

The adversaries this system actually has: **an authenticated member of another workspace**, **an
authenticated member of this workspace acting outside their role**, **a hostile input** (a website,
a repository, a model's output, an uploaded artifact, a submitted patch), and **a compromised
runner**. An external network attacker is not the interesting case; every boundary below is
crossable by someone who is already legitimately inside something.

---

## 2. Boundaries, and what enforces each

### 2.1 Tenant isolation

| Enforced by | Where | Proved by |
|---|---|---|
| Row-level security, `FORCE`d so the table owner is subject to it | `migrations/0001`, `0013` and every table-creating migration | `test_tenant_isolation.py::test_row_level_security_is_forced_not_merely_enabled` |
| Every connection scoped with `SET LOCAL` inside a transaction | `persistence/__init__.py::workspace_connection` | `test_tenant_isolation.py::test_an_unscoped_connection_sees_no_workspace_scoped_rows` |
| Composite foreign keys carrying `workspace_id` | every cross-row reference | FK checks bypass RLS; a single-column FK would accept another tenant's row |
| One 404 for "does not exist" and "not yours" | `api/problems.py::not_found` | `test_boundary_probes.py::test_every_read_route_refuses_a_non_member_identically` — all 20 read routes, one answer |
| A body's `workspaceId` is refused, not ignored | `auth/membership.py::assert_route_matches_body` | `test_boundary_probes.py::test_a_forged_workspace_in_the_body_is_refused_rather_than_ignored` |
| A composed identifier pair is bound before use | `routes/runs.py::_bound_attempt` | `test_boundary_probes.py::test_another_workspaces_attempt_cannot_be_read_through_my_own_run` |

**Residual:** the `workspace` and `app_user` tables are not under row-level security. A connection
can therefore read workspace names and user emails outside its scope. No route exposes either — the
session route joins through `workspace_membership`, whose policy matches only the acting user — but
the database does not prevent it, and a future route could.

### 2.2 Authority and revocation

| Enforced by | Where | Proved by |
|---|---|---|
| Session tokens stored as SHA-256 only | `auth/sessions.py` | `test_auth_boundaries.py::test_the_raw_session_token_is_never_stored` |
| Revocation and expiry checked inside the lookup, not after | `auth/sessions.py::resolve_session` | `test_boundary_probes.py::test_a_revoked_session_stops_working_on_the_next_request` |
| Membership resolved per request, never cached | `auth/membership.py::resolve_human_principal` | `test_boundary_probes.py::test_a_revoked_membership_stops_working_on_the_next_request` |
| An idempotent replay re-checks authority before returning | `dependencies.py::run_idempotently` | `test_http_api.py::test_a_replay_rechecks_authorization` |
| CSRF token separate from the session token, compared in constant time | `auth/sessions.py::verify_csrf` | `test_auth_boundaries.py::test_the_csrf_token_is_not_the_session_token` |

**Residual:** authentication itself is delegated and unimplemented. The only sign-in mode is a
local-development bridge that accepts an email with no secret; `ApiSettings` refuses it outside a
`local` environment, and a deployment with no provider cannot sign anyone in at all.

### 2.3 The navigator's capability boundary

| Enforced by | Where | Proved by |
|---|---|---|
| The navigator policy is built without oracle material, selectors or source | `domain/journeys/compile.py` | `test_authoring_routes.py::test_the_navigator_policy_contains_no_oracle_material` |
| A fixture key cannot be both navigator input and observer expectation | `domain/journeys/dsl.py::FixtureBinding` | `test_authoring_routes.py::test_an_oracle_value_offered_as_navigator_input_is_refused` |
| Selector-shaped text in a task intent is refused | `domain/journeys/dsl.py::TaskIntent` | `test_authoring_routes.py::test_a_selector_in_the_task_summary_is_refused` |
| The action vocabulary and key chords are a fixed allowlist | `domain/journeys/dsl.py`, `validation.py` | `test_authoring_routes.py::test_a_key_chord_that_reaches_the_operating_system_is_refused` |
| Which observer may decide which assertion is a frozen mapping read at access time | `domain/journeys/assertions.py::ASSERTION_OBSERVERS` | reassigning it would change authority without changing the assertion digest; `MappingProxyType` makes it raise |

**Not enforced, because it does not exist:** there is no navigator process. Modules 12–15 are not
built, so nothing has ever *run* under this policy. The boundary is enforced at authoring time and
has never been tested against an agent trying to cross it.

### 2.4 Desktop and lease authority

| Enforced by | Where | Proved by |
|---|---|---|
| One attempt per physical desktop, by partial unique index | `migrations/0008` | `test_runner_control_plane.py` (INV-10) |
| A lease epoch that never resets, so a stale supervisor's credentials never revive | `migrations/0008` | `test_runner_dispatch_authorization.py` |
| Readiness derived server-side from submitted checks, never copied | `persistence/runners.py::record_preflight` | there is no column a runner can set to make itself ready |
| An expired lease quarantines rather than reassigns | `persistence/runners.py::fence_expired_leases` | `test_runner_terminalization.py` |
| Dispatch re-checks the most recent preflight, not any past success | `persistence/runners.py::assert_dispatch_authorized` | `test_runner_dispatch_authorization.py::test_the_most_recent_preflight_is_the_one_that_counts` |

**Residual:** no runner has ever connected. Every property above is proved against records written
by tests. The E0 boundary is **a dedicated, signed-in local desktop**, and that is a physical and
procedural boundary rather than a technical one: nothing prevents an operator from enrolling the
machine they are sitting at, and the reset warning is text.

### 2.5 Evidence integrity

| Enforced by | Where | Proved by |
|---|---|---|
| A single trusted sequencer assigns canonical positions inside a locked transaction | `persistence/sequencer.py::admit_record` | `test_evidence_sequencer.py` |
| Contiguity and producer closure are separate checks | `routes/runs.py::evidence_completeness` | `test_replay_routes.py::test_a_contiguous_chain_with_an_open_producer_is_reported_as_both` |
| Artifact digests computed server-side and re-read on promotion | `persistence/evidence/artifacts.py` | an object swapped under a row is caught because `promote` re-hashes the bytes |
| Terminal runs are immutable, enforced by trigger | `migrations/0003` | `test_journal_crash_matrix.py` |
| Deleting evidence makes finalization count it missing | `persistence/evidence/artifacts.py`, `finalization.py` | `test_evidence_finalization.py` |

**Residual:** the sequencer trusts the authenticated producer's claim about what it observed. It
establishes ordering, deduplication and provenance; it does not establish that a reader said what a
producer says it said. Offline verification inherits exactly this limit, and
`packages/evidence/.../signing.py` reports signature results as attribution rather than validity for
the same reason.

### 2.6 Budgets and metering

| Enforced by | Where | Proved by |
|---|---|---|
| Admission locks the entitlement row, so concurrent requests cannot both find room | `persistence/budgets.py::admit_within_budget` | `test_budgets_and_metering.py::test_concurrent_admissions_cannot_overspend_the_limit` — 8 threads, limit 3, exactly 3 admitted |
| Usage events idempotent by `(workspace_id, event_key)` | `migrations/0013` | `test_budgets_and_metering.py::test_the_same_usage_event_delivered_twice_is_counted_once` |
| A workspace with no entitlement is refused, not treated as unlimited | `persistence/budgets.py::NoEntitlement` | `test_settings_routes.py::test_a_run_cannot_be_requested_before_anyone_configures_an_allowance` |
| Self-reported quantities are marked `ESTIMATED` and still count | `migrations/0013`, `budgets.py::UsageTotal` | `test_settings_routes.py::test_usage_separates_measured_estimated_and_unavailable` |
| No price, currency or payment instrument exists in the schema or any response | `migrations/0013` | `test_budgets_and_metering.py::test_the_schema_holds_no_price_currency_or_payment_instrument` |

**Residual:** only run admission is currently charged. Actions, wall-clock seconds and model tokens
have limits, columns and reporting, and nothing yet records them — because nothing dispatches an
action or calls a model. The limits are enforceable and unenforced, which is a different statement
from enforced.

### 2.7 Secrets and disclosure

| Enforced by | Where | Proved by |
|---|---|---|
| Every refusal goes through one handler; no exception message reaches a caller | `api/app.py` | `test_boundary_probes.py::test_no_response_anywhere_carries_a_credential_or_a_stack_trace` — a canary in 25 paths |
| Validation errors report a count, never a field value | `api/app.py::_validation` | echoing a value is how a body carrying a token reaches a log |
| Diagnostics redact credentials in every environment | `api/config.py::redacted` | `test_boundary_probes.py::test_diagnostics_never_reports_a_credential` |
| Enrollment tokens have no read route at all | route table | `test_http_api.py` walks every path |
| Credential *references* only; no credential value is accepted by any route | `routes/projects.py` | `test_authoring_routes.py::test_a_listing_never_reveals_a_credential_reference` |
| Committed secrets blocked in CI | `.github/workflows/ci.yml`, GitGuardian | the pattern scan runs on every push |

---

## 3. What this model does not cover

These are gaps, not deferrals with dates.

- **No external penetration test has been performed.** Every probe here was written by the same
  person who wrote the code it probes, which is the weakest possible form of adversarial review.
- **No legal or regulatory advice has been obtained.** Nothing in this repository establishes
  compliance with any obligation, and the export screen says so to its reader.
- **No dependency vulnerability scanning beyond lockfile pinning and pnpm's minimum-release-age
  gate.** There is no SCA tool in CI.
- **No privilege-revocation drill against a live deployment**, because there is no deployment.
- **No build-worker egress probe**, because there is no build worker. Module 14's sandbox does not
  exist, so the claim that a build cannot reach the control plane or a metadata service is untested
  in both directions.
- **No rate limiting.** An authenticated member can issue unlimited requests to any read route. The
  budget bounds *work*, not requests.
- **No hosted isolation model.** E0's boundary is one dedicated local desktop. Nothing here has been
  hardened as a fleet, and any statement that it has would be false.

---

## 4. Residual risks, ranked by what they would cost

1. **A false PASS through unverified reader evidence.** The sequencer proves provenance, not truth.
   Mitigated only by there being no reader at all today; the day one exists, this is the risk.
2. **Desktop takeover through an enrollment token.** One-time, short-lived and unreadable, but it is
   the single credential that grants input authority on a real machine.
3. **Cross-tenant disclosure through a route added without the shared refusal.** Every current route
   is covered by one collected probe; a new route is covered only if somebody adds it to the list.
4. **Unbounded request volume.** No rate limit exists.
5. **Workspace and user names readable outside a scope at the database level.** No route exposes
   them; nothing stops one from being written.
