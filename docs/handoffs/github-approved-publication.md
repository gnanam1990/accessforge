# Approved GitHub check publication service

`github_publisher.publish_preview` composes the existing owner-reviewed preview and exact
`GITHUB_PUBLISH` approval with bounded GitHub.com check creation. This is a trusted operator
service function, not a public credential-taking endpoint, scheduler or automatically enabled
publisher. App JWT provisioning and a bounded artifact-store instance are operator inputs.
No navigator/model tool can invoke it or supply its authorization callback.

The service first verifies the current owner/session, binding and unchanged stored preview.
An existing create reservation is refused before any new token request. The transport checks
the App/installation/account/repository and exact commit, using a token requested for only that
repository with `metadata:read`, `contents:read` and `checks:write`. Installation permissions must
actually include checks write; the ordinary public `inspect_repository` path remains read-only.

Immediately before check creation, the service rechecks original retained artifact membership,
metadata and bounded object bytes for completed evaluations. Queued/nonfinal checks have no
evaluated PASS claim. It then reconstructs the preview again, rechecks the original approving
owner and current exact approval, and commits a NEW irreversible reservation through the existing
unique run/App/repository slot. No old reservation can be resumed or dispatched again.

Only then is one check-create POST sent. Its 201 response must match the original App, commit and
approved payload digest. The temporary token must be successfully revoked before confirmation
is returned. Network operations have a shared 30-second deadline and bounded responses; a slow
local authorization/retention callback cannot authorize a POST after that deadline. The operator
must still supply bounded local I/O; this function cannot interrupt arbitrary trusted callbacks.
There is no HTTP retry. Revocation after the final authorization cannot retract a sent request;
remote repository identity and local retention checks are point-in-time observations.

Migration `0069_github_publication_receipt.sql` adds an immutable, workspace-isolated original
creation receipt tied to its original irreversible intent. Run/preview deletion does not erase
that receipt or restore create authority. Workspace deletion remains the explicit cascade bound.
The receipt contains only intent/check IDs, payload digest, observation time and token-cleanup
confirmation; no credentials or remote response text/URLs are persisted.

Lost reservation/create/revocation/receipt-commit responses are unconfirmed. The existing recovery
endpoint resolves the original preview/intent and now includes `original_creation` when a durable
confirmation exists. `remote_outcome` remains UNKNOWN because historical creation is not proof of
current remote existence, contents or uniqueness; `retry_allowed` remains false. The separate
`read_creation_receipt` service allows an authorized owner to retrieve that same historical
confirmation. Missing receipts never prove absence or allow automatic retry. Existing known-ID
read inspection can compare remote payloads, but is not promoted into creation provenance.

Validation: 58 existing read-protocol checks and nine focused retention checks passed, plus six
real isolated approval/intent/receipt/API-recovery integration cases with synthetic GitHub HTTP.
The cases cover success, lost response, revoked approval, mismatched response, failed token
cleanup and read-only checks permission. Six existing preview/retained-evaluation integration
checks also passed. Two additional completed-run cases use real S3-retained artifacts and the
original INCONCLUSIVE evaluation: intact evidence allows synthetic publication, while deletion
of the original object prevents reservation and check creation. Tests prove no duplicate dispatch after reservation, receipt immutability and
cross-workspace isolation. Ruff and strict mypy passed. The generated test database and role were
removed. Migration 0069 was applied only to that isolated database, never a live installation.

Remaining acceptance: provision the real isolated App credential broker/operator invocation,
perform a separately authorized scoped App delivery, and reconcile any genuinely uncertain
remote outcome without guessing. No actual token or check was created during implementation.
Core real VoiceOver/model/baseline/repair/rerun and release acceptance remain independent gaps.

Primary contracts: [check creation](https://docs.github.com/en/rest/checks/runs#create-a-check-run)
and [installation-token scoping](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app).
