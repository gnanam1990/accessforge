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

Official references: [Codex authentication](https://developers.openai.com/codex/auth),
[SDK integration](https://developers.openai.com/codex/sdk), and
[app-server protocol](https://learn.chatgpt.com/docs/app-server).
