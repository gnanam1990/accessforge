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
The backend implements bounded Strands navigation, diagnosis and repair paths, source/build
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
acceptance recording are still outstanding. NVDA support is contract-only. Production
authentication, complete outbound GitHub App publishing and deployed operational acceptance
are not complete. The included reference application is an authorized development fixture;
its injected defects are not discovered customer incidents. Synthetic checks are not actual
assistive-technology results. This project does not certify legal accessibility compliance.

## Technical foundation

Python API/orchestration/build services, PostgreSQL persistence, React/TypeScript web console,
private S3-compatible evidence storage, Strands agent paths, native runner infrastructure and
isolated candidate builds. The [architecture diagram](../../README.md#architecture) describes
the connected design and explicitly distinguishes it from proven runtime acceptance.

## Before publishing

Attach the public repository URL and exact demonstrated revision, the actual working video,
reproducible judging instructions and required account identifiers. Confirm the chosen license,
eligibility and any incorporated prior-work disclosures. Only update the limitations above
when new retained evidence establishes a different state. Follow the
[readiness checklist](SUBMISSION-READINESS.md); drafting this page does not complete it.
