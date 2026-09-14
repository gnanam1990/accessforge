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
The frozen `/form/FIXTURE` navigator URL is now instantiated as a separate `runtimeStartUrl`
field after live planning authority and original policy/fixture checks. The original policy
and its digest are unchanged. Resolution requires the original confirmed setup context,
matching run/manifest/environment/fixture identities, exact approved origin and nonce.
Only the URL enters model context, never setup context, credential references or observer
results. Legacy literal URLs retain their original payload shape; unresolved placeholders
and fresh-fixture environments without confirmation refuse projection.

Fresh-fixture sessions now declare a required `FIXTURE_SETUP` JSON artifact at session
opening. Materialization exports the original confirmed context and observation with their
hashes and run/attempt binding. The finalizer regenerates these exact bytes from the
protected source rows and verifies the retained object against them. Missing, replaced or
deleted setup evidence therefore cannot leave artifact completeness true. This artifact
uses outcome-evidence retention (`READER_SPEECH`), not short-lived diagnostic retention.
It has no synthetic sequencer stream and does not count as a physical reader observation.
Migration 0053 extends the database's closed artifact-kind constraint to admit this kind;
the object-store validator and SQL boundary must agree before materialization can succeed.
Evaluator version is 1.8.0; historical seals are never rewritten to match a newer evaluator.

This establishes retained initial-setup evidence only: it does not populate an observed
environment identity or imply task success. Focused setup tests cover real source snapshots
with a synthetic attempt identifier. Fresh-fixture lifecycle cases also cover real lease/session,
object-store materialization, finalizer refusal for corrupt/deleted bytes, and inconclusive
outcomes without physical-reader proof. These cases require the integration CI services;
they do not establish an actual desktop run. The real operator host entry and physical baseline
and candidate acceptance remain pending. The candidate
gateway deliberately does not expose setup/observer routes. Mandatory setup admission is now enforced for fresh-fixture
environments; this is not a substitute for current approval, physical preflight or outcome proof.

## Isolated candidate seed history

The trusted regression coordinator now commits a `candidate_fixture_reservation` before its
driver sends the private setup request. Migration 0054 binds this one-shot nonce intent to the
original live regression worker, artifact/runtime policy, daemon and all three owned process
identities. The seed must create that exact nonce with HTTP 201; uncertain outcomes are not
retried or replaced. Before endpoint planning, the driver reads initial nonce, template, variant,
creation time and total effect count in one independent PostgreSQL statement. Confirmation
requires zero effects and creation after the committed reservation. A candidate's HTTP body
is never the source of this measurement.

The forced-RLS receipt has immutable context and a one-way pending-to-confirmed transition.
Revoked, expired, fenced or already endpoint-bound workers cannot confirm it. History survives
endpoint cleanup but supplies neither a RUN_EFFECTS approval nor reader/OS evidence. Browser
setup/observer routes remain private. Seed history alone does not satisfy the fresh-fixture
desktop admission gate.

The trusted `accessforge_orchestrator.candidate_fixture_setup` controller now attaches the
original receipt to an existing candidate run only after independent RUN_EFFECTS approval,
live endpoint/build checks, exact reviewed private configuration and queued/unleased state.
It issues no approval, sends no HTTP and performs no reset. Invoke its `--help` for the same
configuration-file and credential-reference arguments as baseline setup; only the product DB
connection is needed. The attachment records the original pre-seed reservation time separately
from the later run-side confirmation. Repeating an identical attachment is idempotent only
while current authority still holds.

A candidate with unbound preview artifact observations cannot be promoted into fresh reader
setup: preview requests may already have used the fixture. Every gateway request records its
pre-request observation, including uncertain requests. The finalizer exports and compares the
full original seed receipt, not just copied hashes, against its protected regression/build/run
lineage. Historical validation remains available after endpoint cleanup without renewing live
authority. This remains point-in-time initial setup evidence, not a guarantee against later
application mutations or proof of successful physical-reader execution.

Candidate navigator projections now resolve the runtime destination only after the live
candidate binding, confirmed setup identity, frozen baseline manifest and sole permitted
environment-digest change agree. The original baseline URL must be the exact approved
`/form/FIXTURE` template. Only the bound IPv4 loopback candidate listener can replace its
origin. The original navigator policy is unchanged. A private construction context enables
this validated candidate URL; it is not a JSON/model field and is not exported in the model
payload. Arbitrary origins, substituted baseline templates and revoked endpoints refuse
projection. The result is a planning destination, not proof that a browser actually reached it.

For confirmed fresh setup, the completion observer now reads fixture identity and count in
the same independent read-only PostgreSQL snapshot. Nonce, template, variant and creation time
must still match the original initial observation. Missing/recreated/substituted fixtures produce
UNKNOWN, never a count of zero. The effect source retains only an identity digest, not the raw
nonce or private configuration. This binds the final sample to the original fixture incarnation;
it does not prove uninterrupted state between samples, full environment identity or actual AT.
Legacy runs without confirmed setup keep their original count-only evidence shape. Two focused
disposable-DB cases exercise original-instance acceptance and same-nonce recreation refusal
through the actual observer and session closure, with synthetic desktop records only.

The finalizer now derives `FIXTURE_INSTANCE` only when this final known sample agrees with the
retained initial incarnation and names the original product fixture. It reconstructs the v2
logical fixture contract from protected configured navigator values, the independently measured
reset variant, and the supported observer configuration; it never copies the expected sealed
fixture digest into observed identities. The nonce-specific binding stays in retained setup and
observer evidence while the logical contract remains comparable across baseline/candidate runs.
Missing legacy proof, unknown observations or unsupported observer configuration leave this
identity absent. Conflicting identity/count/time bindings refuse interpretation. Other missing
identities, full environment evidence and physical reader readiness remain independent gates;
this change alone cannot produce PASS. Existing immutable evaluations are not recomputed.

Focused tests use real loopback TCP with synthetic replies for transport and separate
disposable product/application PostgreSQL databases with in-process HTTP for successful
setup, lost-response reconciliation, revocation, pending lease refusal and immutability.
These are not real screen-reader runs or production deployment evidence.

## Environment producer binding

The queued setup worker reconstructs the complete `EnvironmentSpec` configuration digest while
holding a shared lock on the original environment row, before any application setup request.
Name, allowed origins, reset strategy, credential references and permitted effects must all match
the approved sealed identity, not just the selected origin and two references. The retained setup
context keeps the same digest field and format; existing equal configuration has unchanged bytes.

The independent observer now includes `environmentConfigurationDigest` in its authenticated source
record. This is reconstructed from its protected configured environment and the locally used
observer credential reference, rechecked before and after the application query. It remains present
even when application measurement is UNKNOWN: configuration identity is not application-state proof.
Old records remain unchanged and do not acquire this new witness through replay. No credential
values, fixture nonce or new raw environment fields are published by this addition.

This witness alone is not observed ENVIRONMENT or a new PASS path. Actual host/reader and deployment
acceptance remain unproven; creating a witness starts no reader or live migration.

## Joined configured runtime environment identity

Evaluator1.9.0 joins the original verified fixture setup, final observer configuration witness and
complete per-action runtime preflight coverage. It reconstructs `EnvironmentSpec` from the original
protected environment row; both producer digests and credential references must agree. Every
authenticated action intent must carry the same independently sampled origin as the original
prepared fixture, within the configured allowlist. Missing setup/witness/fixture identity, UNKNOWN
application measurement or incomplete preflight leave observed ENVIRONMENT absent; contradictory
original evidence refuses evaluation. The seal's expected digest is never copied into observed state.

This is a configured environment identity, not a claim that every allowlisted origin was visited,
an independent inspection of secret credential values, or uninterrupted physical-host attestation.
Live expiry/effect permissions remain enforced by the setup, observer and action admission paths;
expiry is not part of the environment configuration digest. Retained old observer records are not
backfilled and immutable evaluations are not rewritten. Other missing model/profile/build identities
and assertion evidence still prevent a complete successful outcome.

Ten focused synthetic verified-input interpreter cases cover producer agreement, forged common
digests, missing coverage, configuration drift and per-action origin divergence. These are not
actual VoiceOver or deployment evidence. Full retained-artifact integration remains subject to CI.
