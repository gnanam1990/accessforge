# `packages/at-adapters`

**Owned by module 08 and 09. Not implemented.**

Guidepup VoiceOver and NVDA adapters. Maps only verified supported APIs to the allowed action vocabulary.

This directory exists so the workspace boundary is explicit from module 01 onward. It contains no
implementation and no placeholder that could be mistaken for one. Adding code here is the job of
module 08 and 09, under its own tests and handoff.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
