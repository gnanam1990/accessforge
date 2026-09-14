# Protected baseline runtime

`execute_baseline_regressions` reads original retained baseline bytes and runs the existing owned
reference HTTP/database harness, without exposing a browser endpoint or starting a reader. It is
a trusted embedding API, not a public client-provided attestation endpoint.

One immutable-input attempt is reserved per baseline build/run. Original queued-run approval,
captured image/daemon/build identity, current archive retention and absence of a desktop lease
are required. Each process plan commits before creation. Creation facts commit separately from
the fresh authorization check that permits activation; late creation/removal facts cannot revive
expired or restored authority. Four exact process receipts and confirmed removal are required
before successful measurements can be retained. The archive is read and verified again before
publication, and the original approval is checked after potentially blocking policy/archive reads.

Validation measurements come from the protected harness, not app stdout or a list of named checks.
They remain bound to the original artifact, harness policy, process identities and run. The
database terminal receipt is immutable. PASSED describes this protected execution only: it does
not change the baseline run outcome or claim a reader assertion passed. Failed or uncertain
execution is not automatically retried. Restore and lease expiry fence it to UNKNOWN.

An unsettled protected attempt blocks desktop lease admission. Reader-session integration will
need to establish its own runtime authority and independently retained evidence; these measurements
are not yet promoted to finalizer assertion inputs. The current embedding API has no browser or
reader session callback.

Focused checks cover the real PostgreSQL/HTTP authority lifecycle with synthetic process receipts,
late creation/removal after expiry, immutable completion, desktop admission fencing, and forward
migration. Separate synthetic coordinator checks verify commit-before-activation ordering and
failure cleanup handling. Actual baseline Docker/S3 execution and VoiceOver acceptance remain
unproven; this implementation does not substitute synthetic test receipts for those requirements.
