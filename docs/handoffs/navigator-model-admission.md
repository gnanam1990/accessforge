# Navigator model consent and one-shot admission

Human `/runs/{runId}/navigator-model-consent` scope, issue, inspect and revocation routes now bind
the exact approved execution seal, full closed provider configuration, per-run call ceiling and
expiry. Issue requires current RUN_APPROVE, CSRF, If-Match, an idempotency key and explicit billable
acknowledgement. Consent discloses task intent, safe fixtures and retained reader speech to the
provider; token holds are not a financial cap. The default-profile preview refuses other sealed
configurations rather than guessing them. A caller reviewing a non-default supported profile may
submit its entire exact configuration, whose digest must match the original seal.

The shared diagnosis/repair model ledger now also counts NAVIGATOR reservations, including every
configured provider attempt (up to 150,000 reserved tokens for navigator calls only; other purposes
retain their 50,000 limit). Uncertain use stays UNAVAILABLE with its full hold, not measured zero.
Reservations cannot be deleted, restarted, completed twice or reissued under a different operation
ID for the same action boundary. Consent cannot be deleted/reissued or un-revoked.

The trusted coordinator supplies a validated projection digest and original reader event/digest
pairs. Reservation rechecks live attempt/session/approval/consent/issuer authority, exact provider
configuration, resolved non-STOP action sequence, original reader pairs and remaining call/budget
limits in one transaction. It must commit before provider construction or invocation. The digest
is a binding to the coordinator's validated projection, not independent proof of model input.

This delivery supplies admission and human HTTP control, not the full navigator loop. Still needed:
the production coordinator connecting retained projection loading, committed reservation, a fresh
pre-call authority check, actual Strands invocation and native dispatch, with invocation-scoped
checkpoint/result metadata. No reservation is a resumable worker lease or a provider-access proof.
No model, OS action, local live migration, deployment or actual reader run was performed.

Local validation is scoped Ruff/mypy, generated contract/client refresh and diff checks. Existing
CI receives the real HTTP/session/database admission and immutable-ledger case with synthetic input,
plus pure profile/budget cases. No local full suite was run.
