# Prompt 08 — Actual macOS VoiceOver execution

**Dependencies:** 00, 07. **Requirements:** FR-004, FR-005, FR-006, FR-015. **Release:** Required E0 and R1.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD platform boundary and TEST-PLAN. Consult current [Guidepup documentation](https://github.com/guidepup/guidepup) and pin verified adapter APIs; do not invent screen-reader methods.

## Copy-paste prompt

```text
Implement AccessForge module 08 only.

Objective: Execute the approved task using real VoiceOver on a pinned macOS/browser configuration and capture actual observations without giving the navigator privileged browser shortcuts.

Owns: packages/at-adapters/voiceover/, macOS supervisor integration, scoped runner bootstrap documentation and actual-AT tests. Do not change sealed assertion/evaluator contracts to accommodate a broken adapter.

Required inputs: Module 00 authorized desktop capability and module 07 supervisor/lease protocol. Use actual Guidepup VoiceOver; @guidepup/virtual-screen-reader may be used in labeled unit tests only. A Playwright accessibility tree is not VoiceOver evidence.

Tasks:
1. Read the installed Guidepup API and current setup guidance. Pin a tested macOS, browser, VoiceOver, Node and package version matrix; record exact configuration rather than claiming universal macOS support.
2. Implement preflight probes for reader availability, required permissions, interactive desktop, keyboard layout, verbosity, browser state and fixture origin. Missing permissions produce explicit unavailable status; never enable OS permissions without operator approval.
3. Map only the canonical NEXT, PREVIOUS, ACTIVATE, TYPE_TEXT, KEY_CHORD, READ_CURRENT, WAIT_FOR_READER_IDLE and STOP actions to verified adapter capabilities. Maintain a per-profile chord allowlist excluding OS escape, developer tools, arbitrary address-bar navigation and clipboard reads.
4. Use a supervisor-only launch/setup phase to open the sealed target before navigation. Any Playwright use for environment setup/diagnostics remains separate and unavailable to navigator tools.
5. Deliver actual reader observations to the navigator through strict projection, including provenance and bounded length. Exclude source, selectors, DOM, screenshots, oracle state and fixture answers from that channel.
6. Fsync local action intent before dispatch; submit authenticated producer source records with IDs, producerSequence/digest and closing watermark. Do not choose canonical sequence/hash or emit observer receipts. Preserve the separate local journal. Speech-capture timeout is unknown evidence, not empty success.
7. Check cancellation/budget before every action. Send current-epoch stop acknowledgement only after in-flight actions resolve; otherwise preserve ambiguity/quarantine. Exercise lease expiry and prove no new commands are admitted; do not call a cancel request physical stop proof.
8. Reset only the owned test browser/session, restore documented reader settings where safe, and run fresh preflight before reuse. Do not kill unrelated applications or disturb the user's active work.
9. Support redacted diagnostics separately from navigator input. If recording needs additional permissions or contains personal data, require consent and record evidence limitations honestly.
10. Run the real reference form-error journey through VoiceOver, including a known broken variant and an authorized functional submission. Preserve actual speech/action traces and versions without labeling this module's raw observations a final verified verdict.

Negative tests: Permission denied, reader stops, focus escapes, forbidden chord, lost action/stop acknowledgement, locked screen, exhausted budget, missing producer tail, forged observer receipt and stale epoch (INV-01, INV-02, INV-06, INV-09, INV-10, INV-13, INV-14). Unit adapter fakes cannot replace these actual-platform checks.

Acceptance: An independently replayable actual VoiceOver trace proves allowed navigation and visible interruption behavior. The supervisor demonstrably denies forbidden capabilities. Full PASS/FAIL admission remains module 11's job.

Stop conditions: Stop the physical test if permission, desktop ownership or safety is uncertain. Do not synthesize speech, inject DOM clicks or silently switch readers to make the test pass.

Handoff: Write docs/handoffs/08.md with exact platform matrix, real command results, trace locations, permissions, observed failures and limitations. Identify whether E0's real-reader capability is proven or blocked.
```
