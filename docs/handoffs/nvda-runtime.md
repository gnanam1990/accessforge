# NVDA narrow runtime build

## Delivered boundary

The NVDA package now exports an independently buildable driver and a lazy factory
for pinned Guidepup 0.34.0. The factory refuses before SDK import while the verified
Windows matrix remains empty. This is not a qualified Windows host or a completed
runner integration. The existing dispatch contract remains blocked.

The driver uses the closed Windows action vocabulary, approved synthetic fixture
text, cancellation, and a fresh trusted readiness callback before SDK input.
Concurrent operations fence the session. Cleanup cannot race an unresolved call,
cannot run before startup dispatch, and cannot replay a failed stop.

READ_CURRENT sends the SDK report-current-focus command rather than returning
cached item text. NVDA+DOWN uses say-all. Both pinned command definitions use
INSERT; CAPSLOCK qualification is not supported by this factory.
The API surface is documented in the
[official Guidepup NVDA reference](https://www.guidepup.dev/docs/api/class-nvda).

## Evidence and ownership

SDK log deltas are explicitly labelled
`SDK_LOG_DELTA_NOT_ACOUSTIC_ATTESTATION`. Missing, reset, duplicate or empty
logs produce UNKNOWN. WAIT_FOR_READER_IDLE also produces UNKNOWN because this
driver has no acoustic idle sensor. No delta is promoted to canonical actual-reader
evidence, and the NVDA benchmark remains unexecuted.

The embedding supervisor must still supply native Windows session, focus,
permission and lease measurements; durable one-shot action admission; operation
deadlines and cancellation; startup ownership reconciliation; and independent
capture provenance. A readiness callback is a trusted integration port, not
itself evidence that those probes exist. An unsettled SDK operation must not be
retried or raced with shutdown.

## Build verification

TypeScript compilation and 27 focused synthetic checks passed, covering the
existing contract plus command mapping, capture ambiguity, policy refusals,
cancellation, overlap, startup denial and stop replay prevention.
Neither NVDA nor VoiceOver was started. No Windows qualification, model call,
deployment or live migration occurred.

R3 remains incomplete until native host composition and a separately authorized
real Windows journey are implemented and qualified. Actual VoiceOver runtime
testing is paused at the user's request; this build does not alter that decision.
