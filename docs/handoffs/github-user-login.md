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

## Required next integration — do not bypass

- Create persistent short-lived OAuth challenges bound to a separate browser
  cookie, original verifier, client/callback configuration and creation time.
- Consume each challenge atomically before token exchange. A failed or ambiguous
  exchange must not roll back consumption or silently retry. No callback-supplied
  verifier or in-process-only state store is acceptable for multiple replicas.
- Bind numeric provider subjects to existing local users through a trusted
  administrative path. Do not auto-link matching email/login or grant workspace
  membership because the OAuth provider authenticated someone.
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
