# Prompt 23 — Honest run replay, speech evidence and failure inspection

**Dependencies:** 10, 11, 19, 21  
**Requirements:** FR-006, FR-007, FR-009, FR-015, FR-017, FR-019; INV-02, INV-03, INV-06, INV-07, INV-11, INV-12, INV-15  
**Owns:** run detail, evidence timeline, accessible replay controls and finding inspection screens

Read [SESSION-HEADER.md](SESSION-HEADER.md), [UI-UX.md](../UI-UX.md), [CONTRACTS.md](../CONTRACTS.md) and evidence/UI cases in [TEST-PLAN.md](../TEST-PLAN.md).

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 23 only.

Objective: Make the actual execution understandable without converting uncertainty into confidence. The operator must be able to distinguish an accessibility failure from a failed test infrastructure run.

Inspect real ingested event envelopes and evaluator results. Use server-provided outcome semantics; do not calculate a separate browser-side PASS from a green-looking last event or completion receipt.

Implementation tasks:
1. Render execution status and deterministic outcome separately. COMPLETED with FAIL is valid; INTERRUPTED or missing evidence must not be disguised as either an accessible journey or a confirmed defect.
2. Show immutable run identity, source/build/journey/profile references, authorization scope and supported-matrix limits. Provide human labels with expandable exact digests for evidence review.
3. Build timeline views for preflight, actions, reader/observer evidence, interruption and budgets. Use trusted-ingestion sequence, not wall-clock sorting. Expose producer provenance and missing closing watermarks; supervisor-origin receipts are not independent observer proof.
4. Provide a semantic ordered event list, keyboard-operable replay controls, transcript search and deep links. Include a complete unvirtualized paginated reading mode alongside any visual virtualization. Do not autoplay audio, rely on drag-only scrubbing or require waveform inspection.
5. Clearly distinguish actual recorded speech, typed fixture data, agent explanation and independent observer diagnostics. Redact private input; do not visually merge inferred narration with source evidence.
6. Render event gaps, delayed artifacts, quarantined uploads, redacted/deleted evidence and unsupported versions explicitly. An empty transcript after a run started is missing evidence, not a valid silent journey.
7. Show frozen assertion results with supported evidence references and the fixed evaluation rule. Unknown required assertions stay INCONCLUSIVE; no percentage or majority score overrides that result.
8. Present findings as CANDIDATE, REPRODUCED, DISMISSED or RESOLVED with their provenance. Reproduction requires a complete valid failed run; reviewer commentary is not a substitute.
9. Show Cancellation requested; waiting for runner acknowledgement until current-epoch stop proof exists. Only proven stop/no-dispatch permits CANCELLED. Ambiguous action/lost stop proof is INTERRUPTED with quarantine. Retry creates a new run, fresh fixtures and approvals, never terminal resume.
10. Preserve user's reading position/focus during SSE updates and reconnects. Allow pausing automatic following without pausing the run. Use UI-UX.md's exact status/outcome semantics, bounded announcements and stale-state banner; reset from an authoritative snapshot when replay coverage is missing.

Required verification:
Use real recorded reference-application events and an intentionally interrupted run. Test a missing sequence, duplicate events, different client clocks, absent artifact, deleted evidence, delayed finalization, forged workspace and reconnect after retention expiry. Verify keyboard/transcript navigation, accessible media controls and focus stability during updates using the supported actual reader. Static screenshots cannot prove this interaction.

Acceptance gate:
A fresh reviewer can state what happened, which exact assertion failed, what evidence is unavailable and what safe action is next. A reader startup failure is visibly INCONCLUSIVE and cannot acquire a REPRODUCED badge through the UI.

Handoff:
Write docs/handoffs/23.md with inspected real run IDs/digests, UI and AT observations, commands/results and missing replay capabilities. Stop after this module.
```
