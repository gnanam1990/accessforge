# Navigator model consent in the run screen

The run screen now offers an explicit navigator model scope review, acknowledged grant and
permanent revocation. This uses the existing model-consent HTTP routes; no button launches a
provider, a runner process, reader startup or a desktop action.

## Decision boundaries

- OWNER and MAINTAINER may review/approve eligible runs. Other readers can inspect retained
  consent and invocation history. The server remains authoritative for roles and execution approval.
- The review displays provider/model/region, the full sealed profile and both digests, reserved
  tokens per call, maximum calls, UTC expiry and billable disclosure. The checkbox starts unchecked;
  changing either limit clears it. A grant uses the reviewed revision and manifest with a fresh
  idempotency key. The server derives the approver from the current session.
- Invalid fields retain linked inline errors and focus the existing ErrorSummary. Pending controls
  remain mounted and disable concurrent submit, close and history refresh. Workspace/run/actor/
  role/session changes discard late component responses; run changes invalidate the reviewed scope.
- A non-secret `navigatorConsentOperation` URL marker is installed before a grant POST. With that
  marker, an unknown write followed by a missing read is reconciliation-only, including close/reopen
  and reload of that address. It is not a global cross-tab lock: the existing one-consent-per-run
  database constraint and idempotency remain the enforcement boundary. A recognised pre-write
  INVALID_INPUT/400 or PERMISSION_DENIED or CSRF_REQUIRED/403 refusal clears only this operation's
  marker and requires fresh explicit review. Conflicts, malformed success, cancelled/unknown results
  and network failures never authorize a replacement through that marked address.
- Stored consent is history, not current authorization or physical readiness. Revocation requires
  confirmation and cannot reissue the grant, stop already-entered provider work or clear unresolved
  holds. Invocation status copy distinguishes STARTED/UNCONFIRMED/RECORDED/NOT_CALLED and never
  claims measured zero cost, task success or a financial cap. No replay control is provided.
- The API adapter validates the closed profile and response identity before display. Canonical
  model digest verification remains server-owned; unknown provider fields are not displayed or
  copied into a grant. Invocation IDs must be unique, but action sequences are not assumed globally
  unique across attempts. Display is ordered by retained creation time, in batches of twenty.

## Verification and limits

Focused component/adapter regressions are in `apps/web/src/screens/navigatorConsent.test.tsx` and
run with the existing Node CI job. They cover exact grant payload/headers, unchecked acknowledgement,
field-error focus, maintainer/viewer roles, explicit revocation, pending controls, unknown write
reconciliation across remounts, pre-write refusal recovery, stale scopes, offline history and closed
response validation. These are synthetic HTTP fixtures, not provider or physical-reader proof.

On 2026-09-13 an isolated temporary Vite preview was inspected with the in-app browser. It imported
the production component/styles and replaced all HTTP with an explicit synthetic in-memory fixture.
Observed: desktop and 375px viewport light/dark rendering, no horizontal overflow at the narrow
viewport (360px content/client width after scrollbar), unchecked acknowledgement, field edit clearing
acknowledgement, invalid-call error-summary focus and link-to-field focus, stored readback, native
revocation dialog and revoked readback. The temporary files, browser tab and Vite process were removed
after inspection. This is not an authenticated live API integration, actual assistive-technology
acceptance, provider invocation, deployment or end-to-end E0 completion.

The real platform readiness matrix, independent observer, scoped execution/reader approvals and
billable-call authorization remain mandatory. This delivery does not alter those gates.
