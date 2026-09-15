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
