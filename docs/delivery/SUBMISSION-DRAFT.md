# AccessForge — submission text draft

**Not submitted. Not a claim of completed E0 acceptance.** Recheck this description against the
exact recorded revision before publication. Do not replace pending results with imagined ones.

## Short description

An evidence-first workspace for reproducing web accessibility barriers, proposing constrained
source repairs, and independently verifying the resulting user journey.

## The problem and who it serves

A web form can look correct while leaving a screen-reader user unable to understand an error
or finish a task. Engineering teams need a reproducible account of the journey, the exact build
under test, and evidence that a proposed repair both improves that journey and preserves normal
application behavior. AccessForge is designed for product engineers and accessibility reviewers
working together on explicitly authorized applications.

## What we built

The repository contains a web console and API for projects, authorized environments, versioned
journeys, runner enrollment, execution records, findings, patch review, comparisons and exports.
The backend implements bounded Codex-backed navigation, diagnosis and repair paths, source/build
identity binding, protected reference-app execution, candidate builds, durable recovery and
deterministic evidence evaluation. These are implemented components, not a claim that the
entire real-reader journey has already passed acceptance.

The intended end-to-end experience is to reproduce an authorized journey with actual assistive
technology, retain its evidence, propose a narrowly scoped repair, obtain human approval, build
the candidate and independently repeat the frozen journey. Results belong to that specific
build, fixture and reader profile. Missing evidence remains inconclusive instead of becoming a
success message.

## How the agent is constrained

The navigator works through restricted, authorized tools. It does not select its own success
criteria, approve its own source patch, or provide the independent application-state verdict.
Source changes and runtime identities are bound to their original execution. A delivery
acknowledgement is not proof of a stopped reader, and an uncertain response is not permission
to replay a potentially consequential action. A human reviews the repair and evidence.

## Current limitations

Actual VoiceOver qualification and a successful connected baseline-to-repair-to-rerun
acceptance recording are still outstanding. NVDA has a narrow command driver and synthetic
checks; Windows host integration and physical qualification remain unproven. GitHub browser
OAuth and owner-workspace access have been exercised on the hosted Railway control plane,
but that does not establish broad production isolation, recovery or capacity acceptance.
GitHub App publication code is separate from browser login; actual scoped installation,
publication and uncertain-outcome rehearsal remain outstanding.
The included reference application is an authorized development fixture;
its injected defects are not discovered customer incidents. Synthetic checks are not actual
assistive-technology results. This project does not certify legal accessibility compliance.

## Technical foundation

Python API/orchestration/build services, PostgreSQL persistence, React/TypeScript web console,
private S3-compatible evidence storage, Codex-backed agent paths, native runner infrastructure and
isolated candidate builds. The [architecture diagram](../../README.md#architecture) describes
the connected design and explicitly distinguishes it from proven runtime acceptance.

## Hosted implementation checkpoint — 2026-09-16

The [hosted console](https://accessforge-web-production.up.railway.app/) was deployed from merged
source `3fe4bb6a37201cba8bcca6a079bb98a5bf1228ee` (through PR #234). Railway deployment
`0a3073ca-beaa-44e8-a8c1-c1794783cddf` reported SUCCESS. Readiness, root and the new JavaScript
asset returned HTTP 200, including the explicit runner-profile selection control. This establishes
only a control-plane/static deployment checkpoint, not working desktop execution or a completed
customer journey. Refresh the live revision before using this dated evidence in a submission.

Owner browser enrollment, the retention editor and candidate qualification provisioning are
separate open PRs #235–237 at this checkpoint; they are not included in that deployed revision.
Do not present an empty workspace, prepared configuration, CI report or synthetic trace as a
completed accessibility run. No submission or public demonstration video has been produced.

## Before publishing

Attach the public repository URL and exact demonstrated revision, the actual working video,
reproducible judging instructions and required account identifiers. Confirm the chosen license,
eligibility and any incorporated prior-work disclosures. Only update the limitations above
when new retained evidence establishes a different state. Follow the
[readiness checklist](SUBMISSION-READINESS.md); drafting this page does not complete it.
