# Candidate one-shot form transport

The coordinator now commits original permission consumption and a request digest before the
gateway can send a bound candidate POST. A lost commit acknowledgement never returns permission
to send. Current worker, supervisor session, run/lease, RUN_EFFECTS approval, manifest and endpoint
bindings are rechecked. Only the original fixture form path and bounded synthetic fixture values
(or empty fields for validation failures) are accepted; duplicate/unknown fields are refused.
Values stay destination-bound: full_name/category/description use their same-named fixture keys;
email accepts only email/email_invalid/email_valid. A value approved for email cannot become a name.

The isolated HTTP call occurs once, within both the existing request deadline and permit expiry.
Fresh deployed-artifact checks bracket it. A separate transaction retains the bounded response's
status and digest, not raw form HTML. This receipt is transport history, not an independent effect
observation or a successful task verdict. Preview requests retain their existing authority checks;
the legacy method-only gate still cannot open bound POSTs. A promotable candidate-session callback
cannot submit preview POSTs before binding, so preview effects cannot race its subsequent seal.

Migration 0047 makes consumption/delivery history non-replayable and response records immutable.
A consumed permit with no retained response remains unresolved. There is no automatic resend,
refund of permission, restart or optimistic success after transport/retention/authority failure.

The native session waits through a machine-authenticated GET status endpoint before reporting a
known action result: either the response was retained, or the unused permission window expired.
This avoids closing an action before an asynchronous browser submit arrives. Polls do not renew
permission or send POSTs. Missing/foreign/unresolved status fences the action. The result writer
independently enforces this rule while holding the action/run locks; ambiguity can still be
recorded and quarantines the run. Once a result closes the action, late POSTs cannot consume it.

Validation locally: scoped Ruff/mypy, desktop build/typecheck, OpenAPI/client generation (107
operations), and diff checks. CI has focused synthetic delivery-state and real-HTTP status-wait
fixtures plus forward migration coverage. Actual matched candidate/reader/form execution, physical
deployment, independent effect proof, and operator reconciliation of unresolved delivery are not
claimed. No local full test suite, reader startup, paid provider or deployment was invoked.
