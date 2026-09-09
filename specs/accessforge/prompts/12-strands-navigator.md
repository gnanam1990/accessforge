# Prompt 12 — Real Strands navigator with constrained tools

**Dependencies:** 07, 08, 11. **Requirements:** FR-005, FR-008, FR-015, FR-021. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD agent boundaries and TEST-PLAN. Verify current [Strands Python APIs](https://strandsagents.com/docs/user-guide/quickstart/python/) and [tool-loading behavior](https://strandsagents.com/docs/user-guide/concepts/tools/) against the pinned installed SDK.

## Copy-paste prompt

```text
Implement AccessForge module 12 only.

Objective: Use an actual Strands Python agent to plan permitted next actions from actual screen-reader observations while keeping execution authority and outcome judgment outside the model.

Owns: apps/orchestrator/navigator/, packages/agent-tools/navigation/, model configuration adapters and adversarial agent tests. Do not expose generic shell, browser, repository or HTTP tools to the navigator.

Required inputs: Module 07 action gate, module 08 actual VoiceOver producer, and module 11 independent finalization. PostgreSQL remains authoritative; Strands conversation/session storage is not the durable business database.

Tasks:
1. Create the navigator using verified Strands Python APIs with an explicit configured model. Pin SDK/model configuration identity in the manifest and document actual service access requirements without exposing credentials.
2. Build a strict projection containing approved task intent, safe fixture values and actual reader observations only. Add structural tests proving DOM, selectors, source, screenshots, observer secrets and answer keys are absent.
3. Register only narrow tool functions that accept canonical allowed actions and sealed run references. Disable automatic discovery/loading from the customer checkout or generated patch directories; untrusted Python files must not become tools.
4. Validate model output against schema and supervisor policy before every dispatch. Prompt instructions are not the enforcement boundary; a malicious tool request must fail even if the model ignores its system prompt.
5. Separate provider credentials from runner and target credentials. The model/orchestrator cannot obtain arbitrary OS, repository-write, GitHub publication or application-observer authority.
6. Implement bounded model calls, action count, wall time, context growth and retries for safe reads. Do not retry ambiguous desktop effects. Exceeding a budget produces a visible stop reason, not a prerecorded fallback answer.
7. Treat page announcements and user-authored content as untrusted data. Preserve their necessary task meaning while preventing instructions inside them from changing policy, tool registration or protected assertions.
8. Persist planning checkpoints and proposed-action metadata with privacy-safe retention. On interruption, create a new linked run after fixture reset and authorization checks; never resume a terminal attempt from a chat transcript.
9. Integrate cancellation and lease fencing between model response and dispatch. A delayed model completion cannot cause a new action after its run was cancelled or lease expired.
10. Execute an authorized real-model/real-VoiceOver form-error journey. Let module 11 determine its outcome; report model/provider unavailability honestly and retain unit fakes only in labeled tests.

Negative tests: Announcement says reveal secrets; model proposes a DOM click or shell command; response contains unknown action fields; tool-loading directory is poisoned; cancelled run receives late tool call; action budget exhausts; provider times out after proposal; observer receipt is requested (INV-01, INV-02, INV-05, INV-08, INV-09, INV-13, INV-14).

Acceptance: Actual Strands decisions drive allowed VoiceOver actions, a forged out-of-policy call is denied below the prompt layer, and outcome is independently evaluated. Record real invocation evidence without claiming framework import success proves agent integration.

Stop conditions: Missing configured model access blocks the real-model gate; missing VoiceOver blocks the actual-AT gate. Do not bypass either with scripted success, broaden capabilities or incur unapproved AWS spend.

Handoff: Write docs/handoffs/12.md with SDK/model profile, tool inventory, prompt-injection tests, actual run IDs, budget behavior and remaining limitations for diagnosis/benchmark consumers.
```

