# Baseline reader cancellation propagation

The synchronous baseline operator now passes its cancellation callback into the original reader
admission/STOP wait. Cancellation before admission refuses without dispatch. After possible
dispatch it raises HandoffUnknown before another STOP query, and rechecks after a query before
accepting its result. No lease release, fabricated STOP, retry or finalization is added.

This is cooperative cancellation at async boundaries, not preemption of a running SDK, native
reader or database call. Existing dispatch/query/overall time bounds remain. The original attempt
must still be reconciled and physical reader cleanup independently confirmed.

37 focused synthetic composition/dispatch checks passed, with Ruff format/lint and strict mypy
for the changed implementation. No physical-reader startup or acceptance result is claimed.

## Execution wait, not dispatch acknowledgement

The default and maximum original-STOP wait are now 1800 seconds, matching the native host's
maximum execution window. The previous hard 60-second ceiling could interrupt a still-authorized
journey while its native execution remained live. The synchronous operator and async waiter use
one shared bound; operators can still request a shorter positive timeout. Delivery acknowledgement
remains at most 10 seconds here (and the existing transport retains its own shorter deadline).

This only changes how long the protected baseline callback may wait for positive original STOP
evidence. It does not renew any lease, extend native/model deadlines, grant model calls, dispatch
again, or turn expiry/cancellation into a successful STOP. Unknown closure still requires explicit
reconciliation. The original default has intentionally changed for both entrypoints; callers that
require a 60-second supervisory wait should pass `timeout_seconds=60` explicitly.

40 focused synthetic checks passed, including acceptance of 61-second and 1800-second configured
waits, the new default, unchanged short delivery acknowledgement, cancellation and no replay.
These checks do not sleep for 30 minutes or claim a real long-running reader acceptance test.
