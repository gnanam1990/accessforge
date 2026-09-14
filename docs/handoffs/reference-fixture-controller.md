# Queued reference fixture setup

This controller prepares a single approved run before any desktop lease. It does not
start an assistive technology, navigate a browser, migrate a live database, or deploy.

Run `python -m accessforge_orchestrator.reference_fixture_setup --help` for arguments.
The trusted operator supplies workspace/run IDs, exact loopback HTTP origin, separate
reset/observer credential references, and a private JSON configuration file containing
`resetValues` and `observerConfig`. Credentials come from `ACCESSFORGE_SETUP_TOKEN`,
`ACCESSFORGE_DATABASE_URL` and `ACCESSFORGE_OBSERVER_DATABASE_URL`; provision a separate
read-only application DB observer role. Never put these credentials in model input.

The original approved environment must select `FRESH_FIXTURE_NONCE`. The original
schema-v2 fixture contract must bind template `service-request`, the complete private
reset map (`variant`: `accessible` or `inaccessible`) and observer configuration with
effect `CREATE_TEST_REQUEST`. Historical contracts are not reconstructed or rewritten.

## Transaction and uncertainty boundary

1. Lock the queued run; recheck its original live approval, environment and contract.
2. Commit an immutable product-side nonce/context reservation before HTTP.
3. Send one bounded, proxy-free, redirect-free setup POST with that nonce. The reference
   app returns 201 for creation or 200 for the same still-empty existing fixture.
4. Independently read the application DB in a read-only snapshot. Require the reserved
   nonce, reference application digest, variant and zero durable effects. Application
   creation must not predate reservation or postdate observation; clocks must agree.
5. Recheck authorization and queued/unleased state, then retain a one-way observation.

Unknown HTTP outcomes leave the reservation pending and desktop lease admission blocked
at both the repository API and SQL trigger. An explicit retry reconciles the same nonce;
it never clears a used fixture or invokes the legacy global reset. Cancellation of the
client cannot undo an app-side commit. Revocation during setup can therefore leave an
empty app fixture, but cannot confirm the reservation or authorize dispatch.

Migration 0051 adds a forced-RLS reservation table and guards. Confirmed observations
and reserved context cannot be replaced or individually deleted. Workspace deletion
retains its existing cascade semantics. Runs without a reservation retain legacy lease
behavior unless the original sealed environment selects `FRESH_FIXTURE_NONCE`.
Migration 0052 makes setup mandatory for that strategy: both a missing reservation and
an unconfirmed reservation refuse lease admission. The repository and SQL trigger call
the same workspace-scoped predicate under the run lock. Existing environment strategies
are unchanged; no historical observation is backfilled.

## Evidence and remaining wiring

The receipt explicitly means `INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION`.
It is a point-in-time measurement, not a promise that no later actor changes the app.
It does not establish VoiceOver readiness, full environment identity, task success or
finalizer completeness. The browser helper now requires the controller's reserved nonce
and never calls global reset. Its setup-credential POST reconciles that same still-empty
fixture and accepts only HTTP 200. HTTP 201 means the original application fixture was
missing and was recreated: browser launch is refused, not treated as recovery proof.
An uncertain reconciliation must be inspected; the helper does not automatically retry.
After launch, the independently observed URL must exactly equal the reserved fixture
URL, not merely share its origin. Synthetic launch callbacks are not browser evidence.

Trusted controller embedding must supply `reservedNonce` from the confirmed observation's
`application.nonce` together with the same approved origin, variant and reference template
digest. This helper is not a new authorization endpoint and must not accept model fields.
The frozen `/form/FIXTURE` navigator URL still needs a protected per-run projection, and
setup evidence still needs retained-artifact/preflight/finalizer wiring. The real operator
host entry remains pending. Mandatory setup admission is now enforced for fresh-fixture
environments; this is not a substitute for current approval, physical preflight or outcome proof.

Focused tests use real loopback TCP with synthetic replies for transport and separate
disposable product/application PostgreSQL databases with in-process HTTP for successful
setup, lost-response reconciliation, revocation, pending lease refusal and immutability.
These are not real screen-reader runs or production deployment evidence.
