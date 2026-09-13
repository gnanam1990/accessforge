# Runtime build evidence

Authenticated per-action preflight retention now resolves an optional immutable build-worker
measurement server-side. No browser/supervisor receipt upload or new evidence-producer ACL exists.
The resolver revalidates the current candidate lease, endpoint and deployment binding; accepts only
the same workspace/run/lease/epoch; and requires measurement after dispatch, no later than the
reported preflight, and within ten seconds. Preview measurements cannot become execution evidence.
Missing samples stay absent. Existing preflight retries return the original event and attachment.

The evaluator reads this attachment only from the five original promoted artifacts after canonical
stream/content verification. Runtime reports must cover every original action, occur between its
intent and result, and contain all required checks. All checks must be TRUE for runtime preflight
to pass. BUILD is observed only when every action has a matching-check measurement and all measured
artifact digests agree. Checks alone, admission metadata, and the seal never supply observed BUILD.

Evaluator semantics are version **1.1.0**. New environment manifests must explicitly select that
version; existing seals and immutable evaluation snapshots are not rewritten. A new finalization
of an older seal reports an evaluator mismatch. There is no hardcoded environment creation default.

This does not establish process-memory attestation, real reader execution, or the remaining source,
environment, runner-profile, journey, fixture and model observations. Such missing identities still
make the overall evaluation INCONCLUSIVE. It does not authorize candidate POST effects or start a
reader/provider/deployment. A live host must obtain a qualifying deployed measurement at the action
boundary; the code does not manufacture one when absent.

Validation: scoped Ruff/mypy and diff checks locally; focused synthetic interpretation cases are
authored for existing CI. No local full test suite, reader or provider invocation.
