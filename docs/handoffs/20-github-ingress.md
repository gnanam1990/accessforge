# GitHub integration — ingress foundation, not shipped publication

Module 20 is still partial. `github_webhooks.authenticate` authenticates a bounded raw
body with HMAC-SHA256 before decoding JSON. Duplicate object keys, non-finite constants,
invalid UTF-8, excessive nesting and malformed numeric installation/repository identities
are refused. The 1 MiB input limit and minimum 32-byte secret are local ingress policies.
The return value contains only the body digest and numeric scope claims, not repository
text, comment instructions or credentials. No HTTP route or secret loading is enabled.

GitHub signs the body, not the delivery/event headers. Delivery ID is transport metadata,
not authorization and not a sufficient replay key. Replaying signed bytes under another
delivery ID produces the same body digest. This function does not implement durable
deduplication, installation ownership, repository allowlisting, current permissions,
disconnect handling, freshness or publication authorization. No caller may dispatch work
from this result alone. Installation-less ping/event processing is intentionally unsupported.

## Remaining implementation

1. Isolated integration-service configuration and repository/installation binding verified
   against current GitHub App API evidence, with workspace RLS and revocation.
2. Transactional delivery/body-digest replay admission and event-specific schema handling;
   event names and payload text do not broaden scope.
3. Immutable source-bound check preview and honest outcome/coverage rendering.
4. Exact current payload-bound GITHUB_PUBLISH approval and outbound rechecks. Existing
   run grants, PATCH_APPLY and human ACCEPT cannot authorize any GitHub write.
5. Durable publication intent, ambiguous-response reconciliation and disconnect/deletion
   behavior. No automatic merge or deployment.
6. Separately approved actual GitHub App round trip; no real integration has been exercised.

## Evidence

19 local fixture-based authentication checks, Ruff and strict mypy across 370 files pass.
These are not actual webhook delivery, installation, credential-isolation or publication
proof. No app was installed, endpoint deployed, token requested or publication attempted.

Primary references: [GitHub webhook signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
and [GitHub Checks API guidance](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-checks).
Checks need a GitHub App's `checks:write`; source reading and patch publication require
separate endpoint-specific permission review before their adapters are implemented.
