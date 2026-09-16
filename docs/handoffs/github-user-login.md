# Optional GitHub user login — transport checkpoint

Status: **PARTIAL, not enabled and not an implemented browser sign-in flow**.
This is app-user identity, not Codex model OAuth, GitHub App publication, or AWS.
The current `ApiSettings.identity_provider` and session routes are unchanged:
only none/local-development are available. No new route, credential, account,
session, deployment or live migration is created by this checkpoint.

## Built transport

`apps/api/src/accessforge_api/auth/github_identity.py` provides configuration,
an authorization URL with S256 PKCE and a zero-scope authorization-code exchange.
The exchange obtains a fresh authenticated numeric subject from GitHub.com `/user`.
Email and mutable login names are discarded, never used for account linking.
It returns no access token, profile body, session, workspace role or membership.

The HTTPS callback is fixed to `/v1/auth/github/callback`; credentials, query,
fragment, whitespace and backslash callback ambiguity are refused. Outbound hosts
are fixed GitHub.com endpoints. Client construction ignores environment proxies,
does not follow redirects and makes no retries. The whole exchange and identity
read have one ten-second cancellation deadline. Uncompressed JSON bodies are
bounded to 64 KiB, duplicate fields are rejected, and refused responses expose
only a generic error. Nonempty token scopes are refused: provision a dedicated
zero-scope OAuth app rather than reuse repository-publication credentials.

These choices follow the [GitHub OAuth web-flow documentation](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps),
including S256 and fresh identity validation on every token exchange.

## Built durable challenge boundary

`auth/github_challenges.py` and migration `0071_github_login_challenge.sql`
add five-minute database-clock challenges. State, browser secret and PKCE verifier
are independent random values; only their hashes and a configuration digest are
stored. The digest also binds client-secret rotation. Raw transient values are
excluded from the challenge object's repr.

Consumption owns an independent connection and commits before returning, so a
later provider failure or outer transaction rollback cannot restore the challenge.
It locks the row before checking the current database time: waiting for another
transaction must not extend expiry. Concurrent consumers have only one winner.
Wrong browser, verifier or configuration attempts cannot consume a valid record.
Database/commit errors fail closed; do not retry an ambiguous operation.

Nine focused tests exercise this boundary against disposable real PostgreSQL,
including concurrent consumers, caller rollback, configuration drift and a
confirmed row-lock wait across expiry. They make no provider/browser/reader calls.
The migration is supplied, not applied to a live deployment. Browser cookie and
route wiring remain absent: these helpers alone are not a sign-in flow.

## Required next integration — do not bypass

- Wire the durable challenges to a separate Secure/HttpOnly/SameSite=Lax browser
  cookie carrying the original verifier and browser secret. Consume before token
  exchange; never accept those secrets from callback query parameters. Add bounded
  challenge creation/rate limiting and expired-row cleanup before enabling routes.
- Use the explicit operator binding and fresh-subject issuance described below;
  never pass a callback/body-supplied subject directly to the issuer.
- Add disabled-by-default validated provider configuration, start/callback routes,
  secure cookie handling, refusal/audit paths, existing-account disable checks,
  session issuance and UI discovery. Preserve local-only login restrictions.
- Update the live OpenAPI contract/generated clients and verify state theft,
  callback replay/races, expiry, provider/config drift, session/account revocation,
  transaction failure and token/error redaction at the real database boundary.
- Register/configure the dedicated OAuth application and test an actual authorized
  browser flow separately. No such registration or provider call was performed.

Twenty-six focused synthetic transport/configuration tests passed before this
handoff, together with scoped Ruff and strict mypy. They include malformed and
duplicate JSON, redirect refusal, oversized responses, scope and subject confusion,
PKCE construction and cancellation of a stalled body. They are not external login
acceptance. HTTPX is now an explicit API dependency; the lock changed only that
dependency metadata, with no new resolved package version.

## Built operator account binding and session provenance

Migration `0072_github_user_identity.sql` creates a one-to-one binding from a
positive GitHub numeric subject to an existing local user UUID. The operator
helper never creates accounts, matches email/login names, grants membership,
overwrites conflicts or reactivates a revoked binding. Existing non-GitHub
sessions remain unbound; the migration invents no identity assertion.

`scripts/github_user_identity.py bind --github-user-id NUMERIC_ID --user-id LOCAL_UUID
--operator AUDIT_LABEL` is an explicit host-operator command. `revoke` takes the
subject and label but no local-user argument. It requires `ACCESSFORGE_DATABASE_URL`
and performs no migration, provider call or automatic setup. **The operator must
independently verify ownership of both identities first.** Database/host access
is the authority; the label is only attribution, not authorization. This is not
a tenant-accessible administration endpoint. No live binding was created here.
Revocation is permanent in this interface; restoration/reassignment needs a
separately reviewed recovery path, not direct SQL instructions in a login route.

After the future route consumes state and obtains a fresh GitHub `/user` result,
`issue_github_session` checks the binding and enabled local account, issues an
existing-format session and commits the success audit in the same transaction.
A `GitHubSubject` object is data, not a signed credential or proof by itself.
Session rows retain the provider subject via a user-matching composite foreign
key; rotation retains that provenance and rechecks the binding/account. Each
resolution checks current binding revocation/account disable state. The operator
revoker revokes associated sessions atomically; it does not revoke independent
local sessions or change memberships. Binding/user/session lock ordering also
covers resolve-then-rotate, which already updates the old session's last-seen time.
Audit write failure rolls back binding, issuance and revocation rather than
leaving unaudited authority. Refusal auditing at the future HTTP boundary remains
part of route integration.

Focused real PostgreSQL coverage includes concurrent conflicting bindings,
issuance/rotation races with revocation, provider provenance after rotation,
disabled/revoked resolution, audit failure rollback and previous-schema session
preservation. CLI unit checks cover invalid subjects/arguments and error redaction.
These are implementation checks, not actual GitHub login or reader acceptance.
