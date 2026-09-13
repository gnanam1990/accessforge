# Retained original-source comparison

The trusted operator can now add `--retain-record` to the comparison command. This is an explicit
database write, unlike export-only `--include-source`. It requires current project-configure
permission before source access and again at persistence. Source is read only through the existing
verified Git object broker; there is no public comparison upload or repository-path API.

The write rechecks current proposal revision/digest, original manifest/tree/commit, source snapshot,
project authorization and repair surface under workspace RLS and shared authority locks. Text,
byte count, SHA-256, file modes, operation and after-content must match their source/proposal
identities. The immutable record has a permanent unique identity per patch/digest/base tree.
Replays return that original record, including an already retired tombstone, rather than replacing it.

The operator prints only metadata unless `--include-source` is also supplied. An interrupted or
failed retained operation reports `RETENTION_UNCONFIRMED`: an unknown commit is not a rollback.
Reconcile with the exact patch read endpoint before retrying; no new identity is needed.

- `GET /v1/workspaces/{workspace_id}/patches/{patch_id}/source-comparison` requires evidence-read.
- `POST .../source-comparison/retirement` requires owner workspace-configure permission, CSRF and
  the exact `comparisonId`. It permanently clears only this derived source copy, not repository
  files, proposal bytes, approvals or outcomes.
- Successful source and retirement responses are `Cache-Control: no-store`. Repository authorization
  revocation retires its retained copies automatically. Reauthorization does not resurrect them.

Migration 0042 enforces workspace isolation, immutable identity and one-way payload retirement.
This migration was authored, not applied to user data. Synthetic CI-only database/API fixtures cover
read permission, owner-only retirement, CSRF, exact target, immutable records, revocation and
non-resurrection. They are not source-broker or actual repair proof. Local checks are limited to
changed-file Ruff/mypy, contract generation and diff validation; local test suites were not run.

The browser comparison controls remain a subsequent slice. No source preparation against user
repositories, model call, candidate execution, native reader session or deployment occurred here.
Retained comparison is neither approval nor verification, and does not complete project acceptance.
