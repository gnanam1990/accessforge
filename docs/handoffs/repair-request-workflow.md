# Human repair request and recovery

Five authenticated routes expose preview, explicit consent, read-only operation recovery, request
inspection and revocation. Preview names original diagnosis/source/evaluation/surface identities,
all source paths, separately reviewed paths and the pinned provider profile. It discloses full-file
and diagnosis transmission and possible billing; both acknowledgements default false. POST accepts
only the exact current scope, explicit billable acknowledgement and a permanent operation key.
Dependency/build scope requires its own acknowledgement, not model self-approval.

- `GET /findings/{finding_id}/repair-options?diagnosisId=...`
- `POST /findings/{finding_id}/repair-requests`
- `GET /findings/{finding_id}/repair-requests/operation?operationKey=...`
- `GET /repair-requests/{request_id}`
- `POST /repair-requests/{request_id}/revocation`

All routes use the workspace prefix. Reads require evidence-read; creation requires current
owner/maintainer configuration authority and CSRF. Requester or owner can revoke. Successful
responses are no-store. HTTP 202 records human intent only; it does not queue/invoke a model.

Migration 0043 retains immutable one-hour scope and permanent operation-key identity under forced
workspace RLS. Generic retry-cache expiry cannot renew consent or create another request. Finding
locking serializes lineage: a follow-up names the latest predecessor; STARTED/UNCONFIRMED cannot be
bypassed with revocation, another key or another diagnosis. A live unstarted predecessor must first
expire or be revoked. Later delivery must recheck current scope and authority before and after work.

The existing invocation ledger gains an immutable purpose discriminator (its legacy SQL table name
remains `diagnosis_invocation`). Old rows default DIAGNOSIS; REPAIR shares the same workspace budget
lock, token limit and unreconciled holds. Usage keys are purpose-specific. An unknown provider call
does not become zero measured usage or release its reservation. This is not a hard financial cap.

Local checks are scoped Ruff/mypy, generated OpenAPI/clients (104 operations) and diff validation.
CI-only real-DB/API fixtures reuse an existing synthetic retained-evidence occurrence to cover consent,
CSRF, exact readback, retry-cache expiry, non-bypassable unknown invocation, immutable revocation,
workspace isolation and shared token accounting. No local suite, model, real reader or deployment ran.
The synthetic diagnosis fixture proves request persistence, not generation readiness or source proof.

Operator delivery is implemented in [repair model delivery](repair-model-delivery.md), including
reservation, post-call checks and atomic PROPOSED persistence. Browser request controls and actual
provider/source acceptance remain required.
No schema migration or billable request was executed against user data during development.
