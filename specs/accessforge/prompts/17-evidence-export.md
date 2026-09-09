# Prompt 17 — Redacted evidence bundles and independent verification

**Dependencies:** 10, 11, 16  
**Requirements:** FR-006, FR-007, FR-012, FR-013, FR-020; INV-03, INV-06, INV-07, INV-11, INV-12, INV-15  
**Owns:** evidence bundle schemas, export jobs, offline verifier and export privacy controls

Read [SESSION-HEADER.md](SESSION-HEADER.md), [CONTRACTS.md](../CONTRACTS.md), the evidence design in [TDD.md](../TDD.md) and corresponding [TEST-PLAN.md](../TEST-PLAN.md) cases.

## Copy-paste prompt

```text
Apply the AccessForge shared session instructions. Execute module 17 only.

Objective: Export independently inspectable evidence while making its integrity, redaction, trust roots and verification limits explicit. A signed archive is not proof that every disabled person can use the application.

Inspect existing event canonicalization, object storage, review records and access controls. Identify exactly which inputs the offline verifier needs and which sensitive contents must never enter a public artifact.

Implementation tasks:
1. Specify a versioned bundle containing sealed run manifests, ordered source events, required artifact inventory, frozen evaluator inputs/results, baseline/candidate comparison, protected-regression results and separately attributable reviews.
2. Include canonicalization/digest versions, producer identities/type admission, original source-record digests/sequences, required closing watermarks, raw/redacted object digests and completeness declarations. Distinguish retained, redacted, deleted and unavailable evidence.
3. Recheck workspace membership and export permissions at request, generation and download. Bind export authorization to the selected exact records; asynchronous completion does not preserve revoked access.
4. Generate bundles from authoritative stored records rather than agent-supplied summary JSON. Recompute server-side hashes and refuse a fully verifiable label when required artifacts or contiguous events are absent.
5. Implement deterministic redaction and explicit replacement metadata. Redaction that removes verification inputs may permit a limited disclosure bundle, but cannot retain a full-verification claim.
6. Build an offline verifier requiring no account/network. Check schemas, canonical chain, each producer's contiguous sequence and closing watermark, allowed producer/type provenance, artifacts, identities, frozen outcome and review attribution where disclosed inputs permit it. A canonical chain missing an observer tail is incomplete.
7. Sign service attestations using the selected supported cryptographic library and a rotatable service key. Treat signatures as issuer attribution, not independent physical truth. Document key identity, rotation and externally supplied trust roots; an embedded public key alone is not independently trusted provenance.
8. Protect archive processing from path traversal, symlinks, duplicate names, oversized objects, decompression bombs and unexpected executable content. Use a bounded safe parser; never execute bundled scripts.
9. Expose machine-readable verification findings and a clear human report: verified aspects, missing aspects, changed/redacted inputs, unsupported versions and limits. Nonzero exit status must accompany failed integrity checks.
10. Record retention/deletion interactions so an old export never misleadingly claims current server retention. Audit downloads without logging private artifact content or signed access URLs.

Required verification:
First create cases for modified bytes, gapped/forked events, forged supervisor-origin receipts, missing observer watermark, wrong workspace, altered identity, missing review, unknown schema, malicious archive paths, expired download and deleted required evidence. Verify canonical JSON vectors in both languages. Run the verifier disconnected from the API; tampered or incomplete bundles cannot pass.

Acceptance gate:
Export a real baseline/candidate run and human assessment where available. A fresh operator can validate the disclosed bundle independently and understand precisely what it does not establish. Synthetic bundles remain useful conformance fixtures but cannot replace the real export gate.

Handoff:
Write docs/handoffs/17.md with bundle digest, verifier commands/results, redaction policy, integrity attack results and remaining real-evidence blockers. Never upload the bundle publicly without separate approval. Stop after this module.
```
