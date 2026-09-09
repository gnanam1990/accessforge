# Prompt 06 — Versioned journeys and protected assertions

**Dependencies:** 02, 05. **Requirements:** FR-003, FR-005, FR-007, FR-023. **Release:** E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD journey/evaluator design and TEST-PLAN. A journey is a bounded test contract, not an unrestricted browser instruction.

## Copy-paste prompt

```text
Implement AccessForge module 06 only.

Objective: Translate an authorized task into a versioned, reviewable journey whose success criteria cannot be changed by the navigator or repair agent.

Owns: journey schemas and authoring services, packages/domain/journeys/, protected assertion definitions, fixture binding and DSL validation tests. Coordinate authoritative schema changes through module 02 generation; do not hand-maintain divergent TypeScript types.

Required inputs: Project/environment capability summaries from module 05. Preserve the canonical separation between safe navigator task input, trusted fixture reset data and privileged completion-observer configuration. Source, selectors, DOM, screenshots and answer keys are forbidden navigator observations in screen-reader-only mode.

Tasks:
1. Implement immutable journey versions with task intent, start condition, supported capability profile, allowed effects, bounded actions/time and required assertion references. Editing creates a new version and digest.
2. Restrict E0 authoring to the real form-error recovery scenario. Define the expected initial fixture state, validation error encounter, recovery behavior and application receipt without hard-coding a successful action transcript.
3. Define protected assertion types covering task completion, required announcement, focus/order behavior, forbidden effects and functional validation. Each assertion declares its trusted observer and what makes its observation unknown or invalid.
4. Build validation for unsupported actions, forbidden key chords, arbitrary URLs, unbounded waits, secret-bearing fixture values and inconsistent platform requirements. Return actionable 422-style capability errors at the service boundary.
5. Create a compilation step that produces sealed navigator policy, assertion-set digest and fixture digest. Reviewer-visible summaries must explain exactly what an approval permits.
6. Keep completion receipts separate from accessibility assertions. A backend row existing cannot prove focus behavior; an expected phrase appearing in a fixture description cannot count as actual reader output.
7. Ensure the assertion set and evaluator version are immutable for a run and inaccessible to patch tools. Proposed task changes are new versions requiring review, never a repair of a failed run.
8. Define deterministic normalization for observed speech only where justified by the pinned profile, preserving original observations. Avoid fuzzy model similarity as the sole acceptance criterion; ambiguous language stays INCONCLUSIVE.
9. Add a fixture reset interface that returns a fresh instance identity and safe navigator values while keeping oracle material private. Verify resets do not affect unrelated tenant data or prior immutable records.
10. Provide examples for valid E0, unsupported platform, unknown observation and forbidden external effect. Document why these examples are specifications, not already executed accessibility proof.

Negative tests: Agent edits protected assertions; hidden selector sneaks into task payload; result receipt leaks to navigator; unsupported chord escapes browser; copied announcement text satisfies a forged observation; required assertion disappears during version update; fixture reset reuses a prior successful receipt; deadline/budget is unbounded (INV-01 through INV-05, INV-08, INV-16).

Acceptance: The same reviewed journey compiles deterministically in repeated runs, produces fresh fixture instances and rejects out-of-policy behavior before dispatch. Truth conditions remain independently testable and no user-editable instruction can weaken protected assertions.

Stop conditions: If an assertion cannot be observed reliably on the selected actual reader, mark it unsupported or explicitly unknown; do not replace it with DOM inspection while retaining a screen-reader-only claim.

Handoff: Write docs/handoffs/06.md with DSL examples, generated digests, capability validation results and the protected interfaces required by runner control, navigator and outcome verification.
```

