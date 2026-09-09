# Prompt 29 — Fresh adversarial specification-to-runtime acceptance review

**Dependencies:** 28  
**Requirements:** FR-001–FR-025; INV-01–INV-16  
**Owns:** local audit/handoff report; application implementation is read-only during this module

Read [SESSION-HEADER.md](SESSION-HEADER.md), [PRD.md](../PRD.md), [CONTRACTS.md](../CONTRACTS.md), [TDD.md](../TDD.md), [TEST-PLAN.md](../TEST-PLAN.md), [SECURITY-PRIVACY.md](../SECURITY-PRIVACY.md) and the release manifest.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 29 only.

Objective: Decide readiness from fresh evidence as a reviewer who does not trust prior success reports. This module is read-only for application source. Write local audit reports and run safe diagnostics; route needed fixes back to their owning modules and repeat the review at the new head.

Inspect exact HEAD and dirty state before using any artifact. Determine whether module 28 claimed E0 or R1 and whether every captured run, build, test and video belongs to the reviewed version. A handoff marked COMPLETE is evidence to investigate, not permission to waive verification.

Review tasks:
1. Trace every applicable FR and invariant through its real production consumer, source implementation, negative control and executed proof. Find unreachable code, route stubs, placeholder adapters and unsupported release claims.
2. Follow the navigator process/tool/network boundary independently. Attempt actual forbidden DOM/source/observer/shell access, keyboard escape and prompt injection through task/page/repository content; do not accept a prompt instruction as enforcement.
3. Recompute outcomes from raw allowed evidence. Challenge missing preflight, producer tails/watermarks, supervisor-forged observer receipts, corrupt artifacts, stale leases and unknown completion. A contiguous ingestion chain alone is insufficient; invalid evidence wins over an apparent receipt.
4. Compare baseline/candidate manifests and protected tests independently. Reproduce an unsafe patch that removes validation/authorization or alters fixture/assertion/evaluator inputs; it must not become VERIFIED.
5. Exercise independent-process crash/cancellation boundaries: persisted intent, lost result/stop acknowledgement, restart, expired lease and competing acquisition. CANCELLED needs actual stop/no-dispatch proof; ambiguity requires INTERRUPTED/quarantine, not replacement or blind action replay.
6. Inspect tenant/approval enforcement across every API/job/runner/object/stream/export/publication path. Revoke ExecutionGrant between child minting and dispatch, mutate exact inputs, and attempt patch/GitHub writes through grant/review authority. Only exact payload-bound GITHUB_PUBLISH permits publication.
7. Review privacy, secret handling, retention/deletion, quotas and restore behavior using concrete probes. Check that an incomplete/redacted export cannot claim fully independently verifiable historical proof.
8. Use the actual application as a fresh operator through keyboard and the declared reader profiles. Inspect setup, failed-form recovery, replay, diff approval, human review and unavailable states; static screenshots do not satisfy this gate.
9. Search for mocked success, canned speech, fixture-only state, swallowed errors, skip markers and production-reachable bypass flags. Distinguish legitimate unit fakes from critical-path substitution, and verify the actual Strands/backend/AT evidence independently.
10. Publish only demonstrated or strongly supported findings locally with severity, exact source location, reproduction, impact, evidence and owning module. Separate blockers from enhancements; record finite prerequisites for an E0 or R1 readiness decision.

Required verification:
Rerun all mandatory release cases and targeted adversarial cases justified by the exact final head. Report tests actually executed, blocked platform/human/external checks and remaining unknowns. A test suite that passes when the relevant guard is removed cannot clear that invariant. A new fix invalidates affected evidence until the owner reruns proof and this review repeats.

Acceptance gate:
Return READY FOR THE DECLARED RELEASE, CONDITIONAL or NOT READY with concrete evidence and exact local/hosted limitations. Never mark complete because time is short, the UI looks polished, generated tests exist or previous prompts produced stubs. Readiness is not authorization to merge, publish, deploy, charge, sign terms or submit.

Handoff:
Write docs/handoffs/29.md with exact reviewed head, findings, commands/results, proof freshness and unresolved gates. Do not modify application source or grant your own exceptions. Stop after the independent decision.
```
