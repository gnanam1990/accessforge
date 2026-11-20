# AccessForge Tester Desk · acceptance contract

### FR-01 — Commission and task contracts

Implement versioned commission authoring with environment, journey, fee and deliverable rubric pinned at publication. Bind each accepted assignment to its exact immutable brief revision.

### INV-01 — Commission and task contracts

Published briefs retain their terms for each accepted assignment.

### FR-02 — Tester eligibility and consent

Implement restricted enrollment, conflicts, scoped visibility, offer expiry and atomic assignment claims.

### INV-02 — Tester eligibility and consent

Only one eligible, consenting tester may claim an exclusive commission.

### FR-03 — Accessible evidence capture

Build keyboard and reader-friendly drafts, step outcomes, permitted attachments and digest-backed uploads.

### INV-03 — Accessible evidence capture

An unauthorized recording never enters the shared report.

### FR-04 — Submission and review

Seal revisions, validate completeness against the rubric, separate task outcome from evidence acceptance, and allow justified revision requests.

### INV-04 — Submission and review

A failed task can earn acceptance of complete testing work.

### FR-05 — Compensation and reconciliation

Create the payable exactly once, implement an approved compensation adapter and reconcile uncertain results.

### INV-05 — Compensation and reconciliation

One accepted assignment can create at most one external compensation intent.

### FR-06 — Client reporting and privacy

Project only consented evidence to a client report and implement redaction and retention operations.

### INV-06 — Client reporting and privacy

Each export is bound to its audience, release and report revision.

### FR-07 — Retest and release comparison

Link follow-up commissions by defect and compare only matching task and environment dimensions.

### INV-07 — Retest and release comparison

A previous finding never implicitly passes a later release.

### FR-08 — End-to-end agency pilot

Exercise a real test browser, a consenting independent tester and an agency reviewer with a restart and a redacted evidence export.

### INV-08 — End-to-end agency pilot

Automation-only evidence cannot populate a human-observed result.

### NFR-01 — Replayable evidence

A reviewer can reproduce the outcome from the exact input revision, actor scope, persisted decision and redacted evidence reference after a process restart.
