# Prompt 22 — Authorized project setup, journey editing and runner readiness

**Dependencies:** 19, 21  
**Requirements:** FR-001–FR-005, FR-015, FR-017, FR-019; INV-01–INV-03, INV-07, INV-08, INV-10, INV-14  
**Owns:** project/environment setup, journey editor, runner inventory and run-admission screens

Read [SESSION-HEADER.md](SESSION-HEADER.md), [UI-UX.md](../UI-UX.md), [CONTRACTS.md](../CONTRACTS.md) and associated [TEST-PLAN.md](../TEST-PLAN.md) cases.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 22 only.

Objective: Let an operator configure a real authorized task and understand whether it can run. Show what is supported and missing before consuming a physical desktop session or model budget.

Inspect the real API/client and shared UI components. Verify the complete configuration-to-run path exists before wiring controls. Missing backend capabilities must be displayed as unavailable rather than simulated by local browser state.

Implementation tasks:
1. Build project/environment setup showing exact authorized URL scope, repository identity, immutable source/build references and test-only effect restrictions. Never imply that entering any public URL grants permission to test it.
2. Provide credential-reference configuration without rendering secret values. Explain the required test-account scope and avoid putting credentials in query parameters, transcripts or browser persistence.
3. Build a readable journey form and optional validated structured editor for intent, safe fixture references, frozen assertions, permitted effects and completion observer configuration. Label generated content Suggested draft—review required. Validate the actual DSL and show actionable field-linked errors.
4. Separate save-as-new-version from run dispatch. Changing an approved journey, fixture or assertion requires a new sealed context; existing runs keep their original version and history.
5. Render runner inventory with OFFLINE, PREFLIGHT_REQUIRED, READY, BUSY and QUARANTINED plus exact OS, reader/browser profile and capability evidence. Do not present the mere presence of a process as successful AT preflight.
6. Show unsupported matrix combinations explicitly. E0 offers the pinned VoiceOver profile only; Windows/NVDA may appear as unavailable until module 09 passes. R2 mobile/PDF capabilities are not runnable options.
7. Build the exact run authorization preview: project, journey/source/profile digests, permitted test effects, budgets and expiry. Request RUN_EFFECTS through the real API, not an unchecked local confirmation dialog.
8. Handle admission failure for stale revisions, missing artifacts, occupied desktop, expired authorization and quota exhaustion. A disabled Run button must have an accessible explanation and a safe next step.
9. Integrate durable events without changing a form being edited or stealing focus. Mark stale runner/configuration data; handle replay reset by fetching the authoritative snapshot.
10. Implement manual trigger and cancellation-request UI for E0; HTTP 202 acceptance is not physical stop acknowledgement. R1 schedule setup previews the ExecutionGrant's selector, version/scope, effects, budget and expiry; display fresh child authorization per occurrence. No grant authorizes repair or GitHub writes.

Required verification:
Exercise actual API-backed project creation, journey versioning, reader preflight display and run acceptance. Negative cases include changed digest after preview, forged workspace, unsupported reader, deleted fixture, stale SSE status, double-click dispatch, expired approval and unapproved application effect. Test keyboard-only journey authoring, error-summary links, screen-reader labels and runner-state announcements. Never use a fake READY badge to clear an integration blocker.

Acceptance gate:
An operator can configure the reference application's real form-error recovery task, inspect the actual supported runner and dispatch one explicitly authorized run. The resulting run ID must exist in PostgreSQL and be visible after page reload; 202 is displayed as accepted, not passed.

Handoff:
Write docs/handoffs/22.md with real route evidence, admission/AT observations, commands/results and E0/R1 capability limitations. Stop after this module.
```
