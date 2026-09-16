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

This is not a complete onboarding flow. Still needed: owner UI create/list/revoke, explicit
authenticated invitation acceptance API/UI, bounded discovery of invitations for the verified
subject, and trusted creation of a new local account from fresh OAuth identity without matching
email or taking over existing bindings. Public callers must never supply their own authentication
subject. HTTP denial audit and CSRF belong to that integration. Do not expose an arbitrary binding
endpoint or call `accept_invitation` before authentication merely because its arguments match.

No production access, credentials, account provisioning, migration or deployment occurred.
Do not deploy code expecting 0073/0074 until live migration authority is obtained and applied.

## Owner API integration

Owner-only GET collection (bounded UUID-keyset pagination), GET item, PUT create and DELETE revoke
now live under `/v1/workspaces/{workspace_id}/membership-invitations`. PUT accepts exactly
`githubSubject` (decimal string, preserving full BIGINT precision), `role`, `ttlSeconds`, `reason`.
It requires If-Match zero and a new client-chosen UUID. An existing ID is not replayed or refreshed;
unknown outcomes require explicit GET. Revocation requires the current pending revision and keeps
the history. Readbacks include server-derived PENDING/EXPIRED/REVOKED/ACCEPTED state, revision and
no-store; single records include ETag. Accepted status is not current membership authority.

Authentication/CSRF precede owner checks and target lookup. Business denials roll back a savepoint
before their DENIED audit commits in the outer transaction. Live owner checks also remain in the
persistence layer. The API does not provision accounts, send email, create bearer invitation links
or expose acceptance before trusted identity integration is ready.

Fourteen focused parser tests and changed Python static checks pass. A PostgreSQL-backed HTTP test
covers CSRF, owner enforcement, create/readback, duplicate refusal, pagination, revoke and committed
denial audits; it is committed for CI, not locally executed. OpenAPI/client contracts regenerated.

## Owner Settings integration

Settings now offers an OWNER-only invitation form, paginated history, selected record readback
and explicitly confirmed revocation. GitHub numeric identity remains a string, validated against
the BIGINT bound without floating-point conversion. Role, lifetime, reason and confirmation are
reviewed together; edits invalidate confirmation. Field-linked error summaries complement inline
feedback. One visible status region announces creation, without a duplicate live announcement.

Unknown or malformed save receipts lock the draft and preserve its exact generated invitation ID.
An explicit GET reconciles the outcome without another PUT; confirmed absence requires another
explicit confirmation before a new attempt. Revoke responses likewise require exact record/revision
and state, or an explicit current-state read. History page changes do not discard selected detail.
Malformed records never provide row actions. The UI states that no email/account/access is created
and that verified recipient acceptance is not yet available in this interface.

The frontend production build and focused synthetic UI checks cover confirmation, exact large
identity, lost/malformed save response, lost revoke response, history pagination, malformed history
and non-owner restrictions. No live invitation or reader operation was performed. Recipient-side
identity provisioning/discovery/acceptance remains the next incomplete integration.
