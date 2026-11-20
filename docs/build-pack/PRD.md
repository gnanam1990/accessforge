# AccessForge Tester Desk · Product requirements

## Product decision

Procure an independent human retest of one user journey on one release, browser and assistive technology combination. Extend AccessForge when the target checkout is provided. Keep repair generation and broad accessibility audits in their existing product boundaries. First support one web journey and a tester-supplied screen reader.

**Buyer and commercial hypothesis.** Web agencies procuring assistive-technology retests for clients. Charge per external review and workspace.

**People and responsibilities.** An agency lead commissions a test and accepts an agreed evidence deliverable. A consenting independent tester selects an eligible assignment, performs the task and controls which evidence they submit. A finance operator reconciles compensation. The client sees only the authorized summary.

## Customer journey

An agency specifies checkout on a release-specific URL, Safari version, VoiceOver, a keyboard-only path, permitted evidence and tester fee. The tester accepts after reviewing consent terms, reports that focus is trapped after the shipping form, attaches an allowed trace and submits. An agency reviewer accepts the complete evidence even though checkout failed. Accounting pays the tester for the work. A fixed release gets its own retest.

## Acceptance scene

Commission AF-21 pays 20.00 USD for a five-step checkout on release r18. Step four traps keyboard focus. Tester marks step four failed and step five unreachable, submits the agreed evidence, reviewer accepts it. Expect task failed, evidence accepted and one 20.00 payable. Repeat the submit and approval after restart: still one payable. Open r19 and show a new pending retest with no inherited pass.

## Behaviors for the team

- **FR-01 · Commission and task contracts.** Implement versioned commission authoring with environment, journey, fee and deliverable rubric pinned at publication. Bind each accepted assignment to its exact immutable brief revision.
- **FR-02 · Tester eligibility and consent.** Implement restricted enrollment, conflicts, scoped visibility, offer expiry and atomic assignment claims.
- **FR-03 · Accessible evidence capture.** Build keyboard and reader-friendly drafts, step outcomes, permitted attachments and digest-backed uploads.
- **FR-04 · Submission and review.** Seal revisions, validate completeness against the rubric, separate task outcome from evidence acceptance, and allow justified revision requests.
- **FR-05 · Compensation and reconciliation.** Create the payable exactly once, implement an approved compensation adapter and reconcile uncertain results.
- **FR-06 · Client reporting and privacy.** Project only consented evidence to a client report and implement redaction and retention operations.
- **FR-07 · Retest and release comparison.** Link follow-up commissions by defect and compare only matching task and environment dimensions.
- **FR-08 · End-to-end agency pilot.** Exercise a real test browser, a consenting independent tester and an agency reviewer with a restart and a redacted evidence export.

The [acceptance contract](CORE-CONTRACT.md) defines the fail/hold conditions for each requirement. Show the operator the responsible actor, decision reason, missing input and next possible action.

## Demand and release evidence

Obtain an agency-controlled test URL with a fixed release, an independent consenting tester using VoiceOver on Safari and agreed compensation. Confirm a supported tester payout route. A Passport merchant endpoint is appropriate only if the tester or authorized compensation provider actually operates one. Native host/real-reader qualification in the historical AccessForge README is incomplete. A local test cannot count as an independent human result.

Track completed cases over attempted cases, human exceptions, time to resolution and direct provider costs. Preserve the denominator and separate demonstration records from pilot records. Compare with the buyer's current manual workflow on the same cases. The adjacent Atlas project is [Accessibility Repair Studio](https://devpost.com/software/accessibility-repair-studio); customer willingness to pay requires pilot evidence.

## Larger team scope

- Assignment marketplace for an agency roster, exact technology matching and conflict declarations
- Consent and retention per evidence type, private workspaces and expiry
- Tester compensation reconciliation, scoped client review and deliverable disputes
- Release comparisons across browser and assistive technology, redacted client exports

These are product surfaces for a substantial team backlog. Size independently deliverable changes from real integrations, acceptance cases and findings. Around 500 meaningful commits is a scope preference. Actual change count follows the implementation.
