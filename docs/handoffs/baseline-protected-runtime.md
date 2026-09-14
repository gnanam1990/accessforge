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

The shared reference harness accepts explicit `endpoint_origin` and `endpoint_fixture_nonce`
inputs for future baseline session wiring. They require an explicit endpoint callback. The gateway
commits the requested IPv4 loopback origin in its plan and binds only that port; an occupied port
fails instead of falling back to an origin the original seal did not authorize. Default candidate
sessions retain their existing ephemeral-origin behavior and unchanged default plan shape. The
endpoint receipt validator rejects a bound origin that differs from the explicit plan. This is
listener/fixture identity support, not yet the baseline session authorization implementation.

`baseline_fixture_runtime` now supplies trusted prepare/reserve/confirm callbacks for that session
wiring. It reuses the original queued-run setup authority and requires the owned harness's
inaccessible variant and exact protected observer configuration. Nonce reservation does not create
an application fixture. The runtime seed reservation must commit before setup HTTP, and confirmation
may only follow the harness's checked 201 response and independent initial-empty SQL observation.
Duplicate setup reservations are refused, not implicitly reseeded.

Confirmed seed observations retain their original runtime/build/epoch/daemon and three-process
provenance. Fixture evidence snapshot generation revalidates that provenance against original rows;
fenced runtime receipts or substituted identities cannot become accepted seed evidence. Existing
externally observed fixtures without owned-runtime metadata retain their original behavior. Focused
new checks use synthetic SQL rows and do not prove actual fixture startup. Endpoint lifecycle,
reader lease admission and complete callback composition remain pending.

`baseline_endpoints` adds durable plan/bind/live/close operations under the original runtime claim.
Endpoint mode must be selected when reserving the runtime; historical executions acquire no
invented listener requirement. Planning checks the confirmed original fixture and current nonce,
empty-state measurement, original three live process receipts and exact approved listen origin.
Binding cannot change the committed origin or plan. Parent expiry/restore fences live endpoints;
late cleanup cannot revive UNKNOWN. Required endpoints must have both binding and cleanup receipts
before protected execution can be PASSED. Failed execution with an unresolved endpoint stays
UNKNOWN. This still does not authorize a desktop lease or navigator request.

Focused real PostgreSQL/HTTP checks now compose original approval, capture/retention, fixture
reservation/confirmation, endpoint plan/bind/close, and protected completion, using synthetic
process/measurement receipts and in-memory storage. Separate endpoint-admission checks cover
changed origin, expired binding, substituted fixture, nonempty state and missing live processes.
Actual baseline listener/reader callback composition and real AT acceptance remain pending.

Focused checks cover the real PostgreSQL/HTTP authority lifecycle with synthetic process receipts,
late creation/removal after expiry, immutable completion, desktop admission fencing, and forward
migration. Separate synthetic coordinator checks verify commit-before-activation ordering and
failure cleanup handling. Actual baseline Docker/S3 execution and VoiceOver acceptance remain
unproven; this implementation does not substitute synthetic test receipts for those requirements.
