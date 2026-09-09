# `packages/clients`

**Owned by module 18. Not implemented.**

Generated API clients and the operator CLI.

This directory exists so the workspace boundary is explicit from module 01 onward. It contains no
implementation and no placeholder that could be mistaken for one. Adding code here is the job of
module 18, under its own tests and handoff.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
