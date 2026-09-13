# Diagnosis request review and durable recovery

The run screen now exposes a bounded diagnosis request form for owners and maintainers on
completed FAIL/INCONCLUSIVE runs. It reads the original evaluation and fixed provider profile;
the human selects an original assertion, exact relative source excerpt ranges and an optional
current predecessor. Reviewing is read-only. A separate, initially unchecked disclosure/cost
acknowledgement is required before recording the request. No model, reader or repair starts here.

Visible labels, field-linked focused error summaries, sequential headings and review focus reuse
the existing accessible form components. Removing a supporting excerpt restores focus to its add
control. Scope details wrap rather than forcing a horizontal page scroll. These are implementation
properties, not proof from an actual assistive-technology session or a browser acceptance run.

Before POST, the page writes only an opaque operation key to its URL. A remount/reload performs
read-only recovery of that original operation, never automatic POST. Pending/failed acceptance is
not labelled rejection. Revocation explicitly does not prove already-entered provider work stopped.
A revoked STARTED/UNCONFIRMED operation does not offer a new-request shortcut. The URL is not an
authorization capability; recovery requires the original requester's current evidence-read access.
No source text, request body, credentials or provider keys are stored in the browser's persistence.

Migration 0041 adds a permanent per-workspace/requester/run operation-key digest and unique index.
This survives the generic 24-hour idempotency cache: the same scope returns the original request
without renewing expiry or undoing revocation; another scope conflicts. Existing legacy requests
keep NULL rather than inventing a recovery key. The new operation GET is in OpenAPI and both clients.
The UI validates returned run/requester/scope binding before rendering an accepted decision.

Validation: changed-file Ruff/mypy, web TypeScript/production build and diff checks. Focused UI
fixtures and PostgreSQL recovery/cache-expiry cases are authored for CI only; no local test suite,
provider invocation, physical reader, real request recording or deployment was performed. Fresh CI
must be checked before merging. Operator-host provisioning and real provider/reader acceptance,
provider-usage reconciliation and release proof remain separate unfinished work.
