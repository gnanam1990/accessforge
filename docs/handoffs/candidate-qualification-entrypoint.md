# Explicit first-profile qualification entrypoint

The candidate runner now has a production caller:

```sh
accessforge-runner --candidate-proof PRIVATE_OPERATOR.mjs --output-dir NEW_PRIVATE_DIRECTORY --allow-reader-startup
```

This command is separate from the production `--native-host` path. It can collect a first-profile
candidate trace before matrix enrollment, but does not modify `VERIFIED_MATRICES`, enroll a runner,
invoke a model, create canonical events, or return a verified verdict. The startup flag is required
before operator import; it is not an OS permission grant or a substitute for live startup approval.

The private module exports `provisionCandidateProof(signal)` and returns `CandidateHostConfiguration`
from `apps/desktop-runner/src/candidate-host.ts`: assigned desktop/reference and common claim root,
physical runtime evidence and required live artifact probe, exact sealed Safari target, approved
synthetic values/actions, bounded duration/action timeout, and fresh startup/action authorization.
Provisioning must be inert with respect to reader actions and honor cancellation. This executes
trusted operator code, not sandboxed model configuration. Do not load candidate-generated modules.

The host assembles the real lazy Guidepup adapter, native Safari and speech-channel probes, physical
preflight, journal durability probe, exclusive desktop claim, and private file-backed candidate
trace. It does not accept a replacement adapter, journal, trace sink or clock from that module.
Runtime reset and stale automation-source evidence still require concrete owned setup/authority;
missing observations remain unavailable rather than being replaced with TRUE defaults.

The output directory must not exist, and its canonical parent must already be private and owned.
Outputs are `journal.jsonl` and `candidate.jsonl`, labelled local unauthenticated candidate evidence.
An interrupted or thrown execution retains the original desktop claim/output for reconciliation.
A blocked-before-start or candidate-complete result may release the claim after runner cleanup.
SIGINT/SIGTERM fence later input; they are not physical STOP proof. Startup/cleanup still depend
on the SDK calls settling, so a stalled SDK operation remains unconfirmed, not completed.

Validation: TypeScript compilation and 40 focused candidate trace/proof, native-host/CLI and desktop
claim checks pass. New cases prove explicit-option refusal before import, private error sanitization,
incomplete-config refusal, assembled-host claim retention on unavailable runtime evidence without
reader startup or replay, and a late action-authorization result fenced after timeout/cleanup.
These are synthetic/local-file checks only; no reader was started. An actual private provisioner,
dedicated session, permissions, physical qualification trace and end-to-end acceptance remain open.
