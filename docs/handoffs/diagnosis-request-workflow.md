# Authenticated diagnosis requests

This slice adds a human request path to the retained diagnosis backend (PR 42). API handlers
never call a provider. A request is an expiring, revocable decision for an explicit operator-owned
worker invocation, not a queued job, completed diagnosis, charged token count or finding verdict.

- GET `/v1/workspaces/{workspaceId}/diagnosis-profile` exposes the exact pinned worker profile and
  digest, including provider, region, context limits and token allowance. It warns about provider
  disclosure and potential billing; these limits are not a hard financial cap.
- POST `/v1/workspaces/{workspaceId}/runs/{runId}/diagnosis-requests` requires a signed-in requester
  with run-request and evidence-read permission, CSRF, an Idempotency-Key, exact manifest/evaluation
  and model-profile digests, assertion, component, bounded excerpts, explicit predecessor (or null)
  and `billableCallAcknowledged: true`. The server chooses the actor and request ID. Returns 202.
- GET `/v1/workspaces/{workspaceId}/diagnosis-requests/{requestId}` reports original request scope,
  expiry, revocation, durable invocation disposition and finding ID if one was retained.
- POST `/v1/workspaces/{workspaceId}/diagnosis-requests/{requestId}/revocation` permanently revokes
  the request for its currently authorized requester. It does not prove a running provider stopped.

Migration 0040 retains immutable request scope under workspace RLS, with a one-hour expiry and
one-way revocation. Source access remains a separately provisioned host-owned frozen allowlist,
never a root directory or arbitrary tool capability supplied by the browser. Original manifest
and evaluation identity, retained evidence and a valid latest predecessor are checked before
acceptance; the worker rechecks the predecessor before spending provider capacity.

`diagnosis.delivery.deliver_requested` loads the stored human decision and supplies the original
actor/run/excerpts/profile to the existing worker. The request is checked again with exact scope
before invocation reservation and before finding retention, including current membership,
expiry/revocation and original evidence/source checks. Reservation prevents replay of uncertain
operations. Revocation during provider work prevents later retention, not already-disclosed input.

Validation is scoped Ruff/mypy plus generated API/client contract updates locally. CI-only checks
reuse the real completed stopped-artifact fixture for request/replay, CSRF, role refusal, readback,
revocation and isolation; pure request cases cover profile alignment and scope widening. No local
full test suite, actual request against user data, billable model call or physical reader was run.

The operator command is tracked separately in [PR 45](https://github.com/gnanam1990/accessforge/pull/45);
it is not dispatched by this API. Browser request/recovery UI is a separate follow-on slice.
Host provisioning, provider consumption reconciliation, actual retained source/model/reader
acceptance and remaining repair/release work are still pending.
The existing internal `deliver` remains a privileged operator-owned library function, not a public
endpoint. There is no automatic dispatch loop in this slice.
