# Prompt 09 — Actual Windows NVDA execution

**Dependencies:** 00, 07. **Requirements:** FR-004, FR-005, FR-006, FR-015. **Release:** Required R1; optional E0.

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), TDD platform boundary and TEST-PLAN. Use verified [Guidepup NVDA support](https://github.com/guidepup/guidepup); Windows support is not inherited from a passing macOS adapter.

## Copy-paste prompt

```text
Implement AccessForge module 09 only.

Objective: Add an independently verified Windows/NVDA profile using an actual interactive desktop. Preserve E0 progress when Windows access is unavailable without misrepresenting R1 readiness.

Owns: packages/at-adapters/nvda/, Windows supervisor integration, scoped bootstrap and NVDA actual-platform tests. Share protocol contracts with module 07, not platform-specific assumptions copied from VoiceOver.

Required inputs: Module 00 capability status, authorized Windows environment and module 07 lease/action gate. This module does not depend on module 08 implementation. Unit mocks are permitted but never count as NVDA integration proof.

Tasks:
1. Verify the installed Guidepup/NVDA APIs and supported setup on the chosen Windows build/browser. Pin and record Windows, NVDA, browser, Node, keyboard layout, reader settings and adapter versions.
2. Implement preflight for an interactive signed-in session, NVDA availability, necessary configuration and browser focus. Explicitly detect lock/disconnect states that invalidate the tested profile; a background Windows service is not automatically a usable desktop.
3. Register the physical session identity and capability profile with module 07. Multiple processes or remote sessions must not create two active attempts controlling the same physical desktop.
4. Map canonical actions through a Windows-specific allowlist. Block operating-system escape shortcuts, arbitrary browser navigation, downloads, shell access, developer tools and clipboard reads from the navigator.
5. Capture actual NVDA observations as authenticated producer source records with producerSequence, stable IDs/digests and closing watermark. Ingestion owns canonical sequence/hash; preserve the separate local journal. Distinguish delayed/repeated announcements from missing capture without manufacturing speech.
6. Keep privileged environment launch/reset and observer channels separate. Navigator input remains safe task values plus actual reader observations, never browser DOM or receipt answers.
7. Persist intent before command execution and preserve ambiguous result state across supervisor crashes. A remote-desktop interruption or automation error must not cause blind replay of an activation/submission.
8. Acknowledge stop only under the current epoch after in-flight actions resolve. A cancel request without acknowledgement remains pending; ambiguity requires INTERRUPTED/quarantine. Reset must prove old commands cannot continue before reuse. Do not alter unrelated sessions or install settings without approval.
9. Run the reference form with known broken and working behavior on this exact Windows/NVDA profile. Compare semantics through frozen assertions, not byte-for-byte equality with VoiceOver wording.
10. Publish supported/unsupported profile results and expose availability to runner matching. Route module 10 ingestion through the shared event protocol; add no separate Windows-only outcome rule.

Negative tests: Screen lock, remote session loss, NVDA absent, focus stolen, missing producer tail, supervisor-forged receipt, forbidden chord, lost stop acknowledgement during activation, stale epoch, duplicate supervisor and keyboard-layout mismatch (INV-01, INV-02, INV-03, INV-06, INV-09, INV-10, INV-13).

Acceptance: Actual Windows/NVDA traces show the authorized journey, denied shortcuts and visible interruption/quarantine behavior. Passing VoiceOver tests and unit-level NVDA fixtures do not satisfy acceptance. Exact profile support is recorded without a blanket all-Windows claim.

Stop conditions: If Windows access or setup authority is unavailable, finish only independent adapter/contract work and mark real integration BLOCKED. Do not install a virtual reader and advertise R1 support; do not block unrelated E0 modules solely because this optional E0 capability is missing.

Handoff: Write docs/handoffs/09.md with actual platform evidence, setup/cleanup effects, shared-protocol compatibility and remaining R1 blockers. State explicitly whether module 25 may count the NVDA benchmark as executed.
```
