# GitHub-bound membership invitations — persistence layer

Migration 0074 and `accessforge_persistence.invitations` implement explicit owner offers bound to
an immutable numeric GitHub subject, role and bounded lifetime (60 seconds–7 days). An invitation
does not grant access. Its ID is a reference, not a bearer credential, and no email is an authority.

Creation/revocation serialize with membership changes using the workspace lock and recheck the
enabled current owner. Acceptance requires a live provider binding to the authenticated local
account, rechecks the issuer's current owner membership and account status, and grants only a
first membership. Existing or revoked memberships are not overwritten or restored. Expiry is
checked after potentially blocking locks; an internal savepoint rolls back the tentative grant
and success audit if acceptance fails. Acceptance and revocation are terminal; replay cannot
refresh an expiry or issue another grant. Workspace RLS has no unscoped subject-discovery bypass.

## Evidence and remaining integration

Changed Python lint/format/type checks pass. PostgreSQL cases cover single use, concurrent
acceptance, tenant isolation, expiry, revocation, wrong identity/account, disabled identity,
revoked provider binding, issuer demotion and prior membership revocation. Forward migration
proof is extended through 0074, including unchanged existing membership and initially empty
invitation storage. These database cases are committed for required CI, not executed locally.

This is not a complete onboarding flow. Still needed: owner API/UI create/list/revoke, explicit
authenticated invitation acceptance API/UI, bounded discovery of invitations for the verified
subject, and trusted creation of a new local account from fresh OAuth identity without matching
email or taking over existing bindings. Public callers must never supply their own authentication
subject. HTTP denial audit and CSRF belong to that integration. Do not expose an arbitrary binding
endpoint or call `accept_invitation` before authentication merely because its arguments match.

No production access, credentials, account provisioning, migration or deployment occurred.
Do not deploy code expecting 0073/0074 until live migration authority is obtained and applied.
