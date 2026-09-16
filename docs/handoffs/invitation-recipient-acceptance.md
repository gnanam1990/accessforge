# Recipient invitation acceptance

`GET /v1/invitation-offers/{workspace_id}/{invitation_id}` reads only the offer whose immutable
GitHub numeric subject matches the caller's **live provider-bound session**. No existing workspace
membership is required. An ordinary local-development session for the same local account is not
provider authentication and is refused. Wrong-subject and unavailable references share a 404;
the response exposes no other invitations or account-discovery mechanism.

`POST .../accept` requires session CSRF, exactly `{ "accept": true }`, and If-Match one from the
pending offer. Identity never comes from the body, email, username or query. A durable principal
rate limit is consumed independently of subsequent refusal; a caller-chosen workspace does not
consume another tenant's bucket. Only a subject-bound offer authorizes a tenant denial audit.

The existing persistence service rechecks issuer ownership, recipient binding/account, membership
absence, revision and expiry. Grant, acceptance, session rotation and scoped rotation audit commit
together. Both cookies are replaced; no credentials are returned in the JSON body. Replayed
acceptance is refused and cannot recreate a revoked membership. Business failures roll back a
savepoint before their denial audit commits. A lost response may leave the previous session revoked:
sign in through GitHub again, then read current offer and workspace membership; never assume failure
or replay acceptance automatically. ACCEPTED offer history is not current membership authority.

## Evidence and unfinished scope

Thirteen focused body/revision parser checks and changed Python lint/type checks pass. OpenAPI and
clients include 128 operations. A PostgreSQL HTTP test covers no prior membership, unverified/wrong
provider identity, cross-workspace substitution, CSRF, forged identity fields, successful acceptance,
cookie rotation, old-session invalidation, replay refusal and committed audit. It is committed for
required CI, not locally executed. No live invitation or account has been accepted or provisioned.

This endpoint serves an **already provisioned** verified GitHub account. Recipient UI, invitation
discovery and safe new-account provisioning from fresh OAuth identity are still required for full
onboarding. Do not broaden existing operator-only binding functions into public caller-ID binding.
No migration is added here. Code still requires schema0074; the unanswered earlier live approval
question covered only0073 and must not be interpreted as permission for0074 or current-main rollout.
