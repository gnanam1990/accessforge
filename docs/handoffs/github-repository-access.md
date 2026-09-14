# GitHub App repository-access probe

`github_access.inspect_repository` is a concrete GitHub.com HTTP adapter, not an enabled
route, installation enrollment flow or publication service. Its inputs must come from trusted
operator configuration: exact numeric App, installation, account and repository IDs, the
reviewed owner/name and an independently provisioned App JWT. No credential file/environment
discovery, installation creation or browser/navigator credential access is added by the probe.
An explicit offline signer is described below; it does not discover or provision a key.

The function refuses before HTTP unless `allow_temporary_token_issuance=True` is explicitly
supplied. This operation is **not entirely read-only**: it creates and revokes a temporary
installation access token. Actual invocation requires separate operator authorization.

## Protocol

1. Read the exact installation and verify App/account identity, unsuspended state and read access.
2. Request one temporary token for exactly the numeric repository ID, with only `metadata:read`
   and `contents:read`. Reject broader returned permissions or invalid/expired expiry.
3. Read the named repository and verify numeric repository/account IDs and exact full name.
   When an exact `commit_sha` is supplied, fetch that Git commit object through the same repository
   endpoint/token, require the returned SHA to match, then recheck repository/account/full-name
   identity to detect a rename or transfer during the commit read. Branch names, shortened hashes,
   uppercase/non-hex hashes and whitespace are refused before issuing a token. Commit messages,
   author information, signature text and tree metadata are not returned to callers.
4. Recheck the installation to detect scope changes/suspension during the probe.
5. Revoke the owned token before returning a metadata-only point-in-time observation.

The token is never returned, persisted or logged by this function. Known-token failure paths
attempt revocation; revocation failure refuses the result. A lost issuance response can leave
an unknown short-lived read token at GitHub: there is no retry and no claim that no token exists.
An observation is not atomic remote-state locking and cannot be reused as publication authority.
Every outbound publication will still need fresh repository/permission and payload approval checks.
An optional commit observation proves only point-in-time retrieval of that exact object through
the checked repository API. It does not prove default-branch ancestry, exclusive repository
ownership (fork networks can share objects), correspondence of deployed source bytes to the tree,
current object-store retention or permission to publish. The optional returned `commit_sha` is
metadata, not an approval; no publication function consumes it yet.

Production fixes HTTPS `api.github.com`, disables environment proxy/credential discovery and
redirect following, requests/requires uncompressed responses, bounds bodies to 1 MiB, uses
5-second HTTP timeouts and checks a 30-second inspection deadline between responses/chunks.
These are cooperative HTTP bounds, not a hard process-level deadline. Cleanup has its own bounded
HTTP attempt. `_transport` is a trusted test seam and must never be populated from request data.

## Evidence and remaining work

46 local mocked-HTTP protocol cases passed. Cases include
scope drift, permission narrowing, missing expiry, redirects, uncertain issuance and failed
revocation, exact commit retrieval, wrong/missing commits, commit redirects and mid-probe transfer.
They establish protocol behavior only: no real JWT, GitHub App, token or repository
access has been exercised. Local durable workspace binding and its authorized connection service
are described in the [module handoff](20-github-ingress.md). Deployed operator key provisioning,
isolated ingress/event handling and exact-payload publication authorization remain incomplete.

## Known check-ID observation

The optional `check_run_id` requires an exact positive numeric ID and `commit_sha`. Only this mode
adds `checks:read` to the one-repository temporary token; ordinary repository/commit probes retain
their original two read permissions. No `checks:write` permission or check create/update call is
implemented. The adapter reads the known check after commit verification and before the final
repository/installation rechecks and token cleanup.

It verifies numeric check/App identity, exact head SHA, AccessForge name/external-ID format and
the returned lifecycle. Unexpected rich text or annotations are refused. The returned observation
contains only the check ID and canonical digest of the create-preview fields: name, head SHA,
external ID, status, non-null conclusion, output title and summary. The caller must compare that
digest with the **original stored request body**; a changed summary produces a different digest,
not an inferred match. Untrusted remote text and URLs never leave the adapter.

This does not establish who initiated creation, uniqueness of an external ID, absence of another
check, full equality of GitHub-owned metadata/URLs, retention or permission to write. A missing
check, redirect, mismatched identity or timeout is unconfirmed, never permission to recreate.
Successful observation requires successful owned-token revocation. Unknown-ID discovery,
durable remote receipts and outbound-controller composition remain unimplemented.

The 18 additional mocked cases cover known-ID digest match/change, wrong check/App/source,
unexpected text/annotations, inconsistent lifecycle, missing/redirected/timed-out reads,
cleanup failure, late transfer/suspension, missing checks permission and malformed scope.
Ruff and strict mypy passed across 388 files. No real check or installation token was accessed.
Protocol: [Get a check run](https://docs.github.com/en/rest/checks/runs#get-a-check-run).

## Offline App JWT signing

`github_app_jwt.sign_app_jwt` accepts explicitly supplied PEM bytes and the configured numeric
App ID. It does not read files/environment variables, generate persistent keys, call GitHub,
print a token or store anything. It uses the existing locked cryptography package as a declared
direct dependency, with RS256 and fixed claims: App ID issuer, iat 60 seconds before local time,
expiry five minutes ahead. No request-selected claims or algorithm are accepted. PEM input is
bounded to 16 KiB; only RSA private keys of 2048–8192 bits are accepted. Encrypted PEMs require
separate trusted operator decryption; the helper does not discover a passphrase.

The result's repr excludes its bearer, but the bearer attribute is still a credential. Do not
serialize, log or expose it to API clients, agents, repository builds or evidence. Python memory
is not claimed to be securely zeroized. Signing does not prove that the key is registered with
the App, that the clock is synchronized, or that an installation is accessible. The concrete
access probe still verifies remote scope and retains its separate temporary-token authorization.

Fourteen offline checks passed with ephemeral test keys, including independent RSA signature
verification, exact claims, redacted repr, invalid IDs/PEMs, weak/non-RSA/encrypted keys and invalid
clock refusal. These keys never leave the test process. No actual App key was loaded or registered.
The signed credential is not yet provisioned by a deployed isolated credential broker; trusted
host key loading/rotation and endpoint composition remain required before enabling connections.

Primary API contracts: [App installation/token endpoints](https://docs.github.com/en/rest/apps/apps),
[repository lookup](https://docs.github.com/en/rest/repos/repos),
[token revocation](https://docs.github.com/en/rest/apps/installations), and
[App JWT requirements](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app).
The optional commit check uses the [Git commit-object endpoint](https://docs.github.com/en/rest/git/commits#get-a-commit-object)
with `contents:read`; it does not create a commit.
