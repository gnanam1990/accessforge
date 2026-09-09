# `packages/persistence`

**Owned by module 04. Not implemented.**

Migrations, repositories and the transactional outbox. PostgreSQL is authoritative.

This directory exists so the workspace boundary is explicit from module 01 onward. It contains no
implementation and no placeholder that could be mistaken for one. Adding code here is the job of
module 04, under its own tests and handoff.

Import rule: no app imports another app's private implementation. Shared behaviour moves into a
`packages/` module with its own contract.
