# GitHub App repository-access probe

`github_access.inspect_repository` is a concrete GitHub.com HTTP adapter, not an enabled
route, installation enrollment flow or publication service. Its inputs must come from trusted
operator configuration: exact numeric App, installation, account and repository IDs, the
reviewed owner/name and an independently provisioned App JWT. No credential file/environment
discovery, JWT signing, installation creation or browser/navigator credential access is added.

The function refuses before HTTP unless `allow_temporary_token_issuance=True` is explicitly
supplied. This operation is **not entirely read-only**: it creates and revokes a temporary
installation access token. Actual invocation requires separate operator authorization.

## Protocol

1. Read the exact installation and verify App/account identity, unsuspended state and read access.
2. Request one temporary token for exactly the numeric repository ID, with only `metadata:read`
   and `contents:read`. Reject broader returned permissions or invalid/expired expiry.
3. Read the named repository and verify numeric repository/account IDs and exact full name.
4. Recheck the installation to detect scope changes/suspension during the probe.
5. Revoke the owned token before returning a metadata-only point-in-time observation.

The token is never returned, persisted or logged by this function. Known-token failure paths
attempt revocation; revocation failure refuses the result. A lost issuance response can leave
an unknown short-lived read token at GitHub: there is no retry and no claim that no token exists.
An observation is not atomic remote-state locking and cannot be reused as publication authority.
Every outbound publication will still need fresh repository/permission and payload approval checks.

Production fixes HTTPS `api.github.com`, disables environment proxy/credential discovery and
redirect following, requests/requires uncompressed responses, bounds bodies to 1 MiB, uses
5-second HTTP timeouts and checks a 30-second inspection deadline between responses/chunks.
These are cooperative HTTP bounds, not a hard process-level deadline. Cleanup has its own bounded
HTTP attempt. `_transport` is a trusted test seam and must never be populated from request data.

## Evidence and remaining work

18 local mocked-HTTP protocol cases, Ruff and strict mypy across 374 files passed. Cases include
scope drift, permission narrowing, missing expiry, redirects, uncertain issuance and failed
revocation. They establish protocol behavior only: no real JWT, GitHub App, token or repository
access has been exercised. Durable workspace installation binding, operator JWT provisioning,
isolated ingress/event handling and exact-payload publication authorization remain incomplete.

Primary API contracts: [App installation/token endpoints](https://docs.github.com/en/rest/apps/apps),
[repository lookup](https://docs.github.com/en/rest/repos/repos),
[token revocation](https://docs.github.com/en/rest/apps/installations), and
[App JWT requirements](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app).
