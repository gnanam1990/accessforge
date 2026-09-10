# `packages/at-adapters`

Guidepup VoiceOver and NVDA adapters. Maps only verified supported APIs to the allowed action
vocabulary.

| Adapter | Owner | Real-reader status |
|---|---|---|
| `voiceover/` | module 08 | **BLOCKED** — VoiceOver has never been configured on this host |
| `nvda/` | module 09 | **BLOCKED** — there is no Windows host on this machine |

Both are contract work: platform matrix, preflight probes, action and chord policy, and the navigator
projection. Neither has ever produced a screen-reader observation, and both gate every real-reader
path behind an `assertRealReaderProven()` that throws.

`nvda/` shares module 07's protocol contracts and deliberately shares none of `voiceover/`'s platform
assumptions: different chords, a different unusable-desktop taxonomy (a disconnected RDP session has no
macOS equivalent), and a cross-reader comparison rule that compares assertions rather than transcript
text.

In each package `VERIFIED_MATRICES` is empty and the profile status is **derived** from it rather than
written alongside it. Claiming support therefore requires adding a real captured trace and flipping the
status in the same commit; neither is meaningful alone.

Neither package depends on `@guidepup/virtual-screen-reader`, and a test in each asserts so. A virtual
reader runs anywhere and produces plausible announcements, which would make both packages look finished
while reporting accessibility results for a screen reader nobody uses.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
