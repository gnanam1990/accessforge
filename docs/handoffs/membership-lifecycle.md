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

Next: authenticated routes with denial auditing, revisioned readback and stable uncertainty
handling; an identity-confirmed invitation/acceptance workflow; then owner Settings controls.
Do not expose email lookup or equate possession of an email address with account ownership.
Do not deploy code expecting migration 0073 to Railway until its live migration is explicitly
authorized and safely applied. Current deployed main through #234 still uses schema 0072.
