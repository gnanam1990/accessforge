# Codex migration — in progress, not completed acceptance

## Owner decision

On 2026-09-16 the owner selected Codex OAuth and explicitly retired Bedrock/AWS as the
product target. This supersedes AWS/Strands-specific product requirements in the original build
pack; it does not drop actual-reader reproduction, constrained repairs, independent reruns,
human review, evidence preservation or the remaining release modules. Original specs, migration
files and historical evidence are not rewritten to pretend they used Codex.

## First execution slice

Diagnosis and repair builders now use the local Codex CLI with ChatGPT-managed authentication,
not Bedrock. The sealed profiles identify `codex-chatgpt`, CLI `0.154.0`, `gpt-6-astra` and explicit
result-admission budget semantics. Profile changes invalidate old request consent digests.
Existing source/evidence validation, single delivery reservations, patch inspection and approval
remain in force. No token is copied, parsed or stored by AccessForge; no API-key fallback exists.

The CLI runs in a temporary empty working directory, with user config/rules ignored, ephemeral
session mode, read-only sandbox, hosted search disabled and executable/integration capabilities
disabled. Only the structured output schema is written there. Service secrets are omitted from
the child environment. Protocol errors, tool items, multiple answers, incomplete completion,
usage outside the admission envelope, timeout or cancellation cannot produce an accepted draft.
Cancellation terminates the original process group; AccessForge does not retry or resume it.

The CLI owns its internal provider retry behavior; a single admitted CLI turn is **not** evidence
of one HTTP attempt. Output/total token limits are checked against reported completed usage, not
enforced as hard provider-side spending caps. ChatGPT quota/credit consumption still applies.
`billableCallAcknowledged` remains the existing conservative usage acknowledgment wire field;
it does not mean API-key billing is selected. This is a private local operator integration, not
an OAuth credential shared with public tenants. Real host tool isolation and full projection
acceptance still require validation; inspecting command arguments alone is not that proof.

## Next required changes

The production Bedrock navigator factory is now retired and refuses before AWS client creation.
Its former factory exists only inside synthetic compatibility tests; old runtime validators remain
to preserve historical evidence semantics. This deliberately does not pretend the Codex navigator
is already implemented.

1. Replace the navigator's Bedrock-only profile, request observer and Strands action dispatch with
   Codex-native model/runtime provenance and bounded proposals through the existing action gateway.
   Do not relabel old Bedrock events as Codex observations or silently reuse existing consent.
2. Remove remaining active Strands/Bedrock dependencies after their navigator consumers migrate;
   preserve historical evidence readers and migration compatibility. No AWS model call is authorized.
3. Exercise actual bounded diagnosis/repair projections, then the real VoiceOver baseline, repair,
   independent rerun and human review. A connectivity response is not an accessibility result.
4. Update active operator/UI provider wording, deployment guidance, requirements traceability and
   runtime version checks; retain PostgreSQL and local S3-compatible storage independently of AWS.

No live database migration, reader startup, OS permission change, deployment or public submission
is part of this code migration. Remaining Windows/NVDA and release acceptance are not dropped.

## Validation checkpoint

69 focused tests passed, including the retired-factory refusal, historical synthetic evidence,
Codex stream rejection, subprocess timeout cleanup, diagnosis validation and repair policy.
Ruff and strict mypy passed for the changed runtime files. A real local OAuth connectivity run
through this adapter returned `{"answer":"ok"}` with no repository or reader data. Initial CLI
probes found and fixed a reserved-provider override and an experimental-feature startup warning;
neither was ignored as successful evidence. This is connectivity proof only, not a real diagnosis,
repair, isolation audit, VoiceOver journey or release acceptance. Exact-head CI is still required.

Review follow-up: the first CI run failed on four missing test type annotations, now corrected.
The real diagnosis/repair schemas also contained defaulted optional properties: the wire-schema
normalizer now requires every property explicitly, preserving nullable types and original domain
constraints. Two focused schema regressions cover nested real models and property-name handling.
A real OAuth call through `RepairWorker.propose` with the synthetic complete-source fixture returned
`PROPOSAL_READY` with one change after existing repair validation. Its explicit meaning remains
`MODEL_DRAFT_NOT_PERSISTED_APPROVED_APPLIED_OR_VERIFIED`; no target file was changed. The 34 focused
Codex/diagnosis/repair tests and strict checks of both modified test modules pass. New-head CI and
the independent navigator migration remain required.

## Navigator proposal/gateway slice

`navigator/codex.py` now accepts one original sealed projection, requires a caller-supplied
invocation authorization callback, waits for the completed Codex structured result, checks the
original run identity, then submits one proposal through the existing action gateway and durable
planning checkpoints. The instance cannot be reused, including after failure. Wrong-run output,
revoked invocation authority, timeout, cancelled output and provider failure never reach dispatch.
The existing Strands-derived tool wrapper is reused only for its guarded `submit` path, not as a
model agent; removing that residual SDK coupling remains part of the full migration.

Six new synthetic boundary cases plus twenty existing navigator checks pass; Ruff and strict
mypy pass. One real Codex call with a synthetic reader projection and synthetic desktop gateway
completed with exactly one dispatch. No actual reader ran, and no model-runtime receipt was
created. The original attempted assertion that fixture values were absent from the model prompt
was corrected to test exact projection equality: this policy deliberately includes approved safe
fixture values, and this change neither filters nor expands that established projection.

This slice is intentionally not selected by the production durable coordinator yet. Next:
define the Codex-native consent/profile and observed CLI-runtime receipt (without inventing HTTP
request IDs, provider model attestation or hard token caps), compose it into reservation, retention
and finalization, then select this navigator in `NativeNavigatorSession`. Do not unblock actual
reader acceptance from a synthetic gateway or a CLI connectivity result.

## Codex consent and runtime composition

The subsequent runtime slice adds a closed Codex profile and a distinct
`CLI_CONFIGURATION_AND_COMPLETION_NOT_PROVIDER_MODEL_ATTESTATION` observation. It contains the
original canonical CLI thread ID, verified CLI version, forced ChatGPT authentication mode,
requested model, zero exit status, completed-turn flag and reported input/output usage. Prompt,
answer, credentials and invented HTTP request IDs are excluded. The parser rejects missing or
duplicate thread starts. Old Bedrock receipt semantics remain unchanged; mixing a Codex profile
into a legacy HTTP receipt is rejected.

The operator now parses Codex profiles, and `NativeNavigatorSession` selects the Codex planner
after exact manifest/profile comparison and committed one-shot reservation. Fresh consent is
checked before invocation and again by the existing dispatch gateway. Completed CLI evidence is
validated before dispatch and retained by the existing original-operation runtime repository.
Model-scope preview selects the exact known profile matching the sealed digest; it never changes
an old seal to Codex implicitly. Historical Bedrock parsing does not re-enable its retired factory.
The hold for Codex is a quota-admission amount, not a currency cap or observed HTTP retry count.

72 focused tests pass, including synthetic coordinator reservation/revocation/receipt-retention
checks and Codex/legacy profile separation. Ruff and strict mypy pass on the 12 changed code/test
files. A real Codex call against a synthetic reader projection and synthetic desktop gateway
completed with exactly one dispatch and the new CLI observation. No actual reader was executed.
New real PostgreSQL/S3 round-trip acceptance of this Codex-specific path, finalization/export
coverage, operator defaults/UI disclosure and removal of residual Strands tool-wrapper coupling
remain to verify. No live migration, deployment or OS permission change occurred.

Official references: [Codex authentication](https://developers.openai.com/codex/auth),
[SDK integration](https://developers.openai.com/codex/sdk), and
[app-server protocol](https://learn.chatgpt.com/docs/app-server).

### PostgreSQL admission correction

The real database check exposed a legacy-only runtime receipt trigger. Forward migration 0070
adds the exact Codex provider/CLI-meaning pair while retaining the legacy provider/HTTP-meaning
pair, immutable receipts, workspace isolation and the original open invocation/profile binding.
No historical migration, consent or receipt is rewritten. The real API/session/reservation and
receipt-retention test now covers both providers: 2 cases pass under a disposable non-superuser
database role. All 39 forward-migration/interruption checks pass in their disposable databases.
The temporary databases and role were removed. These are synthetic receipt integration checks,
not actual reader or live-provider acceptance; no live database migration was performed.

### Finalizer completion uniqueness

Finalization now refuses reuse of one Codex CLI thread completion across independent action
invocations, even when operation IDs and canonical observation digests differ appropriately.
The new duplicate-completion regression failed before the guard and passes afterward. Distinct
Codex completions are accepted, and historical Bedrock interpretation is unchanged. The two
runtime-evidence test modules pass all 59 cases; Ruff and strict mypy pass. This is retained
artifact interpretation coverage, not S3/export or physical reader acceptance.

### Provider-independent action submission

Codex now calls `NavigationActionSubmission`, a plain Python guarded gateway with no Strands
base class, schema registry or streaming adapter. Existing claim-before-validation, cancellation,
durable proposal/result checkpoints and ambiguous-effect fencing are preserved. The historical
Strands tool is an adapter over that same implementation rather than a duplicate authority path.
Four gateway boundary tests run against both implementations. All 58 focused navigator/contract/
coordinator cases pass, as does strict mypy on the navigator package and modified test module.
Legacy coordinator/type imports still load Strands elsewhere; complete dependency removal remains
pending. No provider call, actual reader execution or operating-system change was performed.

The proposal adapter, diagnosis and repair workers now use a local structural admission-limit
type rather than importing Strands for annotations. A fresh-interpreter regression explicitly
blocks every Strands import and successfully loads all three components. Their 35 focused tests
pass; strict mypy passes across all 68 orchestrator source files. This does not remove the
remaining legacy navigator dependency, nor change the token limits into provider spending caps.

Navigator result contracts are now provider-independent as well. Package exports and the
coordinator load historical Strands adapters only on explicit legacy access; normal Codex
navigator, coordinator and operator imports succeed with every Strands import blocked in a
fresh interpreter. Historical adapter tests still pass. The five focused modules pass 77 cases,
and strict mypy passes on 70 source/test files. Packaging still lists the SDK and needs a separate
development-only dependency transition; this import separation is not full dependency removal.

Strands is now pinned only in the workspace development dependency group for historical tests,
not in the orchestrator runtime requirements. The lockfile was regenerated offline without
version upgrades. A fresh frozen no-dev environment installed 51 packages without Strands and
successfully imported the Codex planner, diagnosis, repair, coordinator and operator. A regression
checks both project declarations and locked runtime requirements. boto3/botocore remain for
S3-compatible evidence storage (including local MinIO); this does not enable Bedrock inference.

## Codex consent UI

The browser consent decoder now accepts the exact closed Codex profile and its single quota hold,
while preserving legacy receipt display. Mixed AWS fields, unknown fields, non-integer limits,
inconsistent token envelopes and incorrect holds are rejected. Codex review displays ChatGPT OAuth
and account-usage/result-admission semantics instead of an invented AWS region or bounded retry
claim. Explicit unchecked acknowledgement, linked errors, duplicate-write prevention and unknown
request reconciliation are unchanged. All 13 consent component tests and web TypeScript checking
pass. This uses synthetic API responses; no live consent, model call or reader startup occurred.
