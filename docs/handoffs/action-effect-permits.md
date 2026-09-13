# Action-bound candidate form permission

The machine-authenticated action endpoint `form-effect-permit` accepts only an empty JSON object.
It revalidates the original live RUN_EFFECTS approval/session/lease and current candidate binding,
requires FORM_SUBMIT in the sealed effects, an unresolved dispatched ACTIVATE or ENTER/SPACE chord, a complete
all-TRUE retained runtime preflight, and its original server-resolved build measurement. Admission
checks or supervisor-uploaded artifact metadata are insufficient.

Migration 0046 retains one immutable grant per original action with workspace-scoped RLS and
cross-workspace-safe action/candidate keys. The grant binds the exact endpoint/path, POST method,
manifest, action, lease/epoch and preflight event. Expiry is at most five seconds and capped by
the session, approval, endpoint, lease and original dispatch boundary. A repeated permission request
returns the original expiry; it does not rearm an action. Consumption has a one-way, single-write
column but no HTTP transport may infer consumption merely from permission issuance.

Trusted desktop configuration can opt into `candidateFormEffects`. After the retained runtime
preflight and physical effect guard, the native session requests this permission once, then the
runner rechecks origin before adapter entry. A lost/refused/malformed permission acknowledgement
fences input. Reading, typing, traversal and STOP do not open form-effect permission windows.
No reader is launched by this endpoint.

**Transport continuation remains required:** the candidate gateway still refuses bound POSTs.
Its worker must durably consume the original permit, recheck current authority, issue the POST
at most once, retain response/uncertainty, and close the effect window before releasing actions.
This delivery does not claim form execution, actual-reader proof, completed effect reconciliation,
or a verified repair. It creates no grants for historical runs and applies no live database migration.

Validation locally: scoped Ruff/mypy, desktop typecheck/build, generated OpenAPI/client refresh,
and diff checks. Existing CI covers schema migration plus focused synthetic permission and adapter
ordering cases; no local full test suite or physical/provider/deployment action was invoked.
