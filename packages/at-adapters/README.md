# `packages/at-adapters`

Guidepup VoiceOver and NVDA adapters. Maps only verified supported APIs to the allowed action
vocabulary.

| Adapter | Owner | Real-reader status |
|---|---|---|
| `voiceover/` | module 08 | **BLOCKED** — VoiceOver has never been configured on this host |
| `nvda/` | module 09 | not yet written |

`voiceover/` is contract work: platform matrix, preflight probes, action and chord policy, and the
navigator projection. It has never produced a screen-reader observation, and gates every real-reader
path behind an `assertRealReaderProven()` that throws.

`VERIFIED_MATRICES` is empty and the profile status is **derived** from it rather than written
alongside it. Claiming support therefore requires adding a real captured trace and flipping the status
in the same commit; neither is meaningful alone.

The package does not depend on `@guidepup/virtual-screen-reader`, and a test asserts so. A virtual
reader runs anywhere and produces plausible announcements, which would make the package look finished
while reporting accessibility results for a screen reader nobody uses.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
