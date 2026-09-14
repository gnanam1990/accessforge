# GitHub integration — ingress foundation, not shipped publication

Module 20 is still partial. `github_webhooks.authenticate` authenticates a bounded raw
body with HMAC-SHA256 before decoding JSON. Duplicate object keys, non-finite constants,
invalid UTF-8, excessive nesting and malformed numeric installation/repository identities
are refused. The 1 MiB input limit and minimum 32-byte secret are local ingress policies.
The return value contains only the body digest and numeric scope claims, not repository
text, comment instructions or credentials. No HTTP route or secret loading is enabled.

GitHub signs the body, not the delivery/event headers. Delivery ID is transport metadata,
not authorization and not a sufficient replay key. Replaying signed bytes under another
delivery ID produces the same body digest. The authentication function does not implement
installation ownership, repository allowlisting, current permissions,
disconnect handling, freshness or publication authorization. No caller may dispatch work
from this result alone. Installation-less ping/event processing is intentionally unsupported.

## Transactional replay inbox

Migration 0065 adds immutable, workspace-RLS authentication receipts, scoped by GitHub App.
The trusted persistence function records one body digest and all observed delivery-ID aliases
in the caller's transaction. Concurrent deliveries of the same bytes admit one body. Reusing
any recorded alias for different bytes is refused and its savepoint rolls back the new body.
Transaction rollback does not consume a delivery. Workspace deletion cascades these receipts;
ordinary update/deletion is refused. No payload text or webhook secret is stored.

These are authentication/replay receipts, not work claims. The integration service must obtain
workspace/App scope from trusted receiver configuration and check current installation/repository
authority before event processing. No public ingress route or event worker is enabled. A receipt
must never be used as proof that an external write has or has not happened; restoration, deletion
and remote-operation reconciliation need independent authorization/publication records.

Four local integration checks passed against a newly created disposable PostgreSQL database:
reconnect/alias replay, concurrent admission, RLS/immutability/cascade and rollback. The database
was removed afterwards. No live migration was performed. This does not prove actual GitHub access.

## Remaining implementation

1. Isolated integration-service configuration and repository/installation binding verified
   against current GitHub App API evidence, with workspace RLS and revocation. The
   [concrete access probe](github-repository-access.md) now implements the HTTP protocol;
   operator JWT provisioning and its authorized connection workflow are still missing.
   Migration 0066 and `github_bindings` now provide the local binding storage described below;
   the trusted connection service now composes current local authorization, the concrete HTTP
   probe and binding/audit commit. No user-facing connection route is enabled yet.
2. Connect the replay inbox to the isolated ingress and event-specific schema handling;
   event names and payload text do not broaden scope. Replay storage alone cannot authorize work.
3. Immutable source-bound check preview and honest outcome/coverage rendering.
4. Exact current payload-bound GITHUB_PUBLISH approval and outbound rechecks. Existing
   run grants, PATCH_APPLY and human ACCEPT cannot authorize any GitHub write.
5. Durable publication intent, ambiguous-response reconciliation and disconnect/deletion
   behavior. No automatic merge or deployment.
6. Separately approved actual GitHub App round trip; no real integration has been exercised.

## Local repository binding storage

Migration 0066 retains exact App, installation, numeric account/repository and owner/name scope
under forced workspace RLS. The trusted integration service must obtain workspace authorization
before calling `record_verified` with a successful access-probe result. This low-level storage
function is not a public authentication boundary. It stores no JWT, private key or installation
token. Original observation time must be within the database clock's preceding 30 seconds.

One live binding exists per workspace/App/repository. Conflicts refuse rather than replacing
an installation, owner or observation. All identity fields are immutable; disconnect is a
one-way local revocation. Reconnection requires a new identity and fresh authorized workflow;
an old binding ID remains revoked. Workspace deletion cascades the local records. Disconnect
does not uninstall the App, undo remote writes or establish remote token revocation.

`require_live` locks and rechecks the current local row in the caller's transaction. Keep this
transaction short and do not hold it over network calls. Recheck after remote observations;
neither an old stored observation nor a live row is current GitHub access or publication
authority. No outbound dispatcher consumes this table yet, so cancellation of publication
intents remains part of the pending publication workflow, not proven by this storage delta.

Four focused checks passed on a newly created disposable PostgreSQL database: reconnect,
conflict/disconnect/new binding identity, database-clock freshness, workspace RLS and immutable
scope/revocation/cascade, and rollback (several assertions share one case). The database was
dropped afterwards. Strict mypy passed across 376 files. These tests use synthetic GitHub
observations; no actual access token, installation, live migration or publication was invoked.

## Authorized connection service

`github_connections.connect_repository` accepts a principal already authenticated by trusted
session middleware and an operator-configured App scope/JWT. The caller must never construct
that principal from a request body. The service rechecks current membership, enabled user and
unexpired/unrevoked session before HTTP, then again after inspection and successful token cleanup.
The database role, not the principal's cached role, must hold WORKSPACE_CONFIGURE (currently owner).
A pre-existing live binding refuses before any token request; the unique index also arbitrates
concurrent connection attempts. The final identity insert and metadata-only success audit commit
atomically. Local authorization locks are released before network I/O; no transaction spans HTTP.

`disconnect_repository` rechecks the same local authority, irreversibly revokes the exact binding
and records the action atomically. A repeated disconnect does not add a second success audit.
It does not cancel a separately requested future connection, uninstall the App or undo any remote
write. No publication workflow consumes these bindings yet. Failed/ambiguous remote operations
are not retried; an uncertain DB commit requires reconciliation, not automatic reconnection.

Nine focused real-PostgreSQL/synthetic-HTTP checks pass for connection/probe/cleanup/binding/audit,
duplicate refusal, disconnect, in-probe membership/session revocation, user disable, role demotion,
cleanup failure and non-owner roles. The temporary database was dropped. This is composition
evidence, not an actual GitHub App round trip. The offline [App JWT signer](github-repository-access.md)
now supplies bounded local signing, not a deployed credential broker. Isolated key provisioning,
authenticated endpoint/CSRF/idempotency routing, denial auditing and connection reconciliation
remain to be integrated before exposing this service. No credentials were requested or used live.

## Evidence

19 local fixture-based authentication checks, Ruff and strict mypy across 370 files pass.
These are not actual webhook delivery, installation, credential-isolation or publication
proof. No app was installed, endpoint deployed, token requested or publication attempted.

Primary references: [GitHub webhook signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
and [GitHub Checks API guidance](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-checks).
Checks need a GitHub App's `checks:write`; source reading and patch publication require
separate endpoint-specific permission review before their adapters are implemented.
