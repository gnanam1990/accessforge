# `packages/evidence`

**Owned by module 10 and 17. Not implemented.**

Evidence manifests, redacted export and the offline verifier.

This directory exists so the workspace boundary is explicit from module 01 onward. It contains no
implementation and no placeholder that could be mistaken for one. Adding code here is the job of
module 10 and 17, under its own tests and handoff.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
