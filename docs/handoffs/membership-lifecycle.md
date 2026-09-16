# Membership lifecycle persistence

`accessforge_persistence.memberships.change_membership` implements owner grant, role change,
revocation and restoration for an **existing exact account UUID**. This is not an invitation or
account-registration flow, and is not yet exposed through HTTP or the Settings UI.

Migration `0073_membership_revision.sql` adds a positive per-membership revision. Zero in a change
request means no membership existed; restoring a revoked row requires its actual revision.
Updates preserve the membership row and append an ALLOWED audit event in the same transaction.
The caller must use a workspace-scoped transaction and must authenticate/CSRF-check any HTTP user.

Workspace row locking serializes these membership decisions. Live, enabled owner membership is
checked after that lock; stale revisions and missing/disabled target identities are refused.
The last enabled active owner cannot be revoked or demoted through this service. Shared account
row locks prevent a concurrent account-disable update from invalidating the check mid-decision.
External privileged account administration is separate and must preserve its own invariants.

Changed Python formatting, lint and mypy passed. Two PostgreSQL integration cases are committed
for required CI: grant/re-role/revoke/restore and stale/revoked-authority checks, plus concurrent
self-demotions retaining one owner. They were not executed locally; no production identity,
membership, database or migration was modified.

The following API integration is now implemented on the follow-on branch:

- OWNER-only `GET /members/{user_id}` includes revoked records, revision and ETag with no-store.
- `PUT /members/{user_id}` requires a live authenticated session, CSRF, current owner role,
  positive If-Match and explicit `{role, reason}`. Null role revokes. It re-roles/restores/revokes
  existing relationships only; there is no arbitrary-account grant endpoint.
- Policy/role/revision refusals roll back a nested savepoint before a DENIED audit is committed
  in the outer transaction. Unauthenticated/CSRF failures stop before this tenant business audit.
- Unknown write outcomes require an explicit GET; no automatic retry or idempotent-success
  claim is offered. Self-demotion/revocation may remove the caller's subsequent read authority.

Thirteen parser tests and changed Python static/contract checks pass. A real HTTP/PostgreSQL case
for CSRF, exact revision, stale retry, revoke/restore, unknown target and persisted denial audit
is committed for required CI, not locally executed. No real access or database changes occurred.

Next: an identity-confirmed invitation/acceptance workflow; then owner Settings controls.
Do not expose email lookup or equate possession of an email address with account ownership.
Do not deploy code expecting migration 0073 to Railway until its live migration is explicitly
authorized and safely applied. Current deployed main through #234 still uses schema 0072.
