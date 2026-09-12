# Remaining work, priority-ordered

Baseline: `main` at `7c9e0bf` (PR #30 merged). This file is the working inventory; `PLAN.md` stays
the module-to-requirement map and `STATUS.md` the evidence record.

`PLAN.md` rows 08, 12 and 13 are **stale**: PR #30 landed the VoiceOver runner boundary, the
constrained Strands navigator and the bounded diagnosis projection. Their rows still read
"not started". Corrected as part of P1's handoff.

## Buildable now

### ~~P1 — Purge pipeline operations~~ — built, awaiting review
Branch `feat/purge-queue-operations`. All four acceptance criteria met; see `STATUS.md`.
Nothing pushed. **Next unblocked slice is P2.**

<details><summary>Original entry</summary>

The four debts `STATUS.md` records against FR-020, all still present on `7c9e0bf`.

| # | Debt | Acceptance criteria |
|---|---|---|
| 1 | No worker drains the purge queue. A deletion the object store could not finish waits for a human to call the retry route. | A worker sweeps every workspace with pending keys, on an interval, with graceful shutdown. Discovery refuses to run as a role that cannot bypass RLS, because an invisible queue and an empty one are the same query result. |
| 2 | `purge_pending_objects` holds one transaction across up to 200 blocking store calls; `purge_until_drained` loops up to 50 such passes inside one transaction. | A drain commits per batch. A failure partway through does not roll back keys already purged — today it does, leaving bytes gone and the queue still claiming them. |
| 3 | `evidence_object_purge.artifact_id` has no foreign key, so nothing stops it naming a non-existent or cross-workspace artifact. | Composite `(artifact_id, workspace_id)` FK, added `NOT VALID` then validated. Composite because FK checks bypass RLS. |
| 4 | No test drives the deletion route with an `Idempotency-Key`. | A replayed key returns the stored response and records no second deletion. The known gap — phase one commits on its own connection, so a request failing after it leaves a deletion with no idempotency record — is tested for what it actually does, not asserted away. |

**Depends on:** nothing. **Risk if skipped:** the product promises bytes are removed and, after any
store outage, nothing removes them.
</details>

### ~~P2 — Module 14/15 routes~~ — built, awaiting review
Branch `feat/m14-m15-patch-and-verification`. Six routes, the patch path policy, `PATCH_APPLY`
approval persistence with a dispatch recheck, and every gate that refuses VERIFIED. Handoffs in
`docs/handoffs/14.md` and `15.md`.

**What is deliberately not built:** module 14's sandbox and candidate build (needs a containment
boundary), and module 15's rerun (needs a real reader). **Runtime proof is BLOCKED** and the VERIFIED
path is exercised with evidence the tests name `_fabricated_`. Nothing is marked verified.

Largest remaining hole: the gates trust what a caller reports about a candidate run. When a real
runner exists those fields must come from evidence, not claims.

Maintainer review round 1 closed three defects: the proposal now persists and reloads exact change
bytes, modes and deletions; `baseSourceDigest` is verified against the source snapshot the manifest
sealed; and the dispatch approval check compares against the patch's current revision, with approval
minting reordered after the transition so a legitimate approval stays dispatchable.

Round 2 closed a fourth: the digest check on load was conditional on there being changes, so an
emptied proposal passed it while keeping its approval.

### P3 — Rate limiting (module 26 gap: "no telemetry or rate limits")
Per-principal and per-workspace limits on the write routes, with RFC7807 `429` and a stated
retry-after. **Depends on:** 18. Security-relevant and self-contained.

### P4 — Structured telemetry (module 26 gap)
Request and outcome telemetry with no evidence content and no object keys in it.
**Depends on:** 18.

### P5 — Python dependency scanning in CI
The Node side is covered by dependency review; Python is not. **Depends on:** 01.

### P6 — `mypy` over `tests/`
139 strict errors, almost all bare `dict` annotations, in the suite that is the evidence for
everything else. **Depends on:** nothing. Mechanical but large.

### P7 — `fixture_digest` offline-guess exposure
An unkeyed SHA-256 over low-entropy fixture values; anyone holding an export can test guesses
offline. A keyed commitment was refused because CONTRACTS requires offline verification. Open
trade-off needing a decision, not an implementation. **Depends on:** a decision from the owner.

## Blocked — not buildable here

| Module | Needs |
|---|---|
| 08 real VoiceOver execution | A Mac with VoiceOver automation grants |
| 09 real NVDA execution | A Windows host with NVDA |
| 12, 13, 14 real-model proof | Bedrock credentials **and** separate spend approval. The code boundaries exist and are tested against fakes; no run has invoked a real model. |
| 20 GitHub checks | GitHub App authorization |
| 25 fault laboratory | 12 proven against a real model, plus a real reader |
| 28, 29 release proof and final review | Everything above |
| All E0 acceptance | An authorized target application |

Nothing in P1–P6 requires any of these.
