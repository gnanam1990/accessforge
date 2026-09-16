# Optional GitHub user login — browser-route checkpoint

Status: **HTTP flow and provider-aware UI implemented, disabled by default; actual OAuth acceptance pending**.
This is app-user identity, not Codex model OAuth, GitHub App publication, or AWS.
`ApiSettings.identity_provider` accepts none/local-development/github. No live
configuration, credential, account, deployment or migration was changed. The
GitHub option is separate from the owner's chosen Codex model provider.

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
The migration is supplied, not applied to a live deployment. The browser-route
integration below now composes these helpers; real provider acceptance is separate.

## Required next integration — do not bypass

- Use the explicit operator binding and fresh-subject issuance described below;
  never pass a callback/body-supplied subject directly to the issuer.
- Verify trusted TLS/proxy forwarding, per-source edge admission controls, and
  query/cookie log redaction before any exposed deployment. Global admission
  limits are resource bounds, not per-client fairness or complete DoS prevention.
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
leaving unaudited authority. The HTTP boundary adds generic refusal auditing
without provider/query/cookie data.

Focused real PostgreSQL coverage includes concurrent conflicting bindings,
issuance/rotation races with revocation, provider provenance after rotation,
disabled/revoked resolution, audit failure rollback and previous-schema session
preservation. CLI unit checks cover invalid subjects/arguments and error redaction.
These are implementation checks, not actual GitHub login or reader acceptance.

## Built provider-aware sign-in UI

`GET /v1/auth/options` returns only the configured provider enum with no-store;
it contains no callback URL, account, membership or credential data and does not
need a session or database query. The web sign-in screen waits for this discovery
before rendering a credential form. `local-development` retains its existing
labelled email form and focusable error summary; `none` offers no form. GitHub
offers a native keyboard-focusable link to the hardcoded same-origin
`/v1/auth/github/start`, with text explaining the GitHub round trip and required
operator binding. It never accepts a redirect target from the discovery payload.

Unknown/malformed responses and network failures display an explicit retry notice,
not an inferred provider or a transient email form. Unmount cancels discovery and
late replies are ignored. Existing sign-out-unconfirmed warnings remain outside
the provider gate. This UI requires the matching API version; a missing discovery
endpoint is an unavailable state, not permission to fall back to passwordless login.

Thirty-seven focused shell checks and fifteen configuration/discovery/contract
checks passed, plus a fresh TypeScript check and Vite production build. The new
shell cases cover GitHub link keyboard focus, absent/malformed providers, explicit
retry and loading without a form flash. These use jsdom and synthetic API replies;
they are not real-browser, GitHub account, or actual screen-reader acceptance.

## Built browser HTTP flow

`ACCESSFORGE_IDENTITY_PROVIDER=github` requires all three dedicated values:
`ACCESSFORGE_GITHUB_OAUTH_CLIENT_ID`, `ACCESSFORGE_GITHUB_OAUTH_CLIENT_SECRET`, and
`ACCESSFORGE_GITHUB_OAUTH_REDIRECT_URI`. Partial or inactive-provider credentials
are refused at configuration construction. The secret is excluded from repr and
diagnostics. No values were installed in this development machine's configuration.

- `GET /v1/auth/github/start` requires the configured HTTPS origin and refuses
  cross-site starts/unexpected query parameters. It creates the committed challenge
  and returns a 303 GitHub authorization redirect plus a short-lived
  `__Host-accessforge_github_login` cookie (Secure, HttpOnly, SameSite=Lax, Path=/,
  no Domain) containing the independent browser secret and original verifier.
- `GET /v1/auth/github/callback` rejects duplicate/oversized/unknown query fields,
  duplicate/malformed binding cookies and origin mismatch. It commits state
  consumption before contacting the provider, handles provider denial without an
  exchange, obtains fresh numeric identity and issues the bound audited session.
  No callback-supplied verifier, subject, return URL or account ID is accepted.
  Success redirects only to `/`; tokens do not appear in the response body.
- Success and handled failures carry no-store/no-referrer. Every handled callback
  response clears the transient cookie. Refusal audit metadata is fixed and errors
  are generic; database failures never print a DSN or raw database diagnostics.
- The existing POST email login refuses in GitHub mode, preserving the local-only
  passwordless bypass rather than accidentally widening it to hosted identity.

Admission is serialized across replicas with a nonblocking database advisory
lock. At most 120 challenges are created per rolling minute and at most 1000
records retained before admitting another. Each attempt removes up to 1000 expired
records; cleanup commits even on a capacity refusal. Contention/capacity returns
429 with Retry-After. This global cap is not a per-source edge/WAF policy and does
not bound all malformed-request audit traffic. Without traffic, expired rows can
remain until the next creation attempt; they are never valid for authentication.

The OpenAPI contract marks both 303 routes as browser navigation and unauthenticated.
Generated Python/TypeScript JSON clients retain the paths but deliberately omit
the operations. A browser must navigate rather than fetch and follow a cross-origin
provider redirect as JSON. The Python server entrypoint disables raw access logs
because query strings contain OAuth code/state; route-template telemetry remains.
Any alternate ASGI launcher and every proxy must also suppress/redact query strings,
cookies and authorization headers. Trust forwarded scheme/host only from the
explicitly controlled TLS proxy, never from arbitrary client-supplied headers.

Focused ASGI tests use real disposable PostgreSQL with a synthetic provider result.
They prove database/cookie/route composition, not actual GitHub authorization,
cross-browser cookie behavior, deployment readiness, or VoiceOver/NVDA execution.
