# AccessForge Tester Desk · Test plan

Every test begins NOT_RUN. [Registry](requirements.json) links each test to one module and its requirement. Run fast domain tests first, then real database/worker integration and normal browser entry points. External integration evidence states its environment and provider.

## Reference end-to-end acceptance

Commission AF-21 pays 20.00 USD for a five-step checkout on release r18. Step four traps keyboard focus. Tester marks step four failed and step five unreachable, submits the agreed evidence, reviewer accepts it. Expect task failed, evidence accepted and one 20.00 payable. Repeat the submit and approval after restart: still one payable. Open r19 and show a new pending retest with no inherited pass.

Run from a clean checkout. Capture the action through the UI or supported API, restart at an uncertain boundary, inspect the recovered record with a second authorized session and export the allowed outcome. Verify a different tenant cannot access it. Exercise the chosen provider against an authorized sandbox/test environment; a local boundary emulator is suitable for deterministic fault injection. Qualify each result by environment.

## Module tests

### T-001 — Commission and task contracts • expected behavior

Status: NOT_RUN

Publish r18 with five steps and reopen the exact brief version.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-002 — Commission and task contracts • failure boundary

Status: NOT_RUN

Change the fee after acceptance and show that the original assignment still carries 20.00.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-003 — Tester eligibility and consent • expected behavior

Status: NOT_RUN

A consenting tester matching Safari and VoiceOver receives the assignment.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-004 — Tester eligibility and consent • failure boundary

Status: NOT_RUN

Race two eligible claims and verify that only the winner can access the test credentials.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-005 — Accessible evidence capture • expected behavior

Status: NOT_RUN

Reload a partially completed draft and recover three observations.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-006 — Accessible evidence capture • failure boundary

Status: NOT_RUN

Upload a disallowed recording type and verify that no downloadable artifact is created.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-007 — Submission and review • expected behavior

Status: NOT_RUN

Submit a focus trap plus unreachable downstream step and accept the evidence.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-008 — Submission and review • failure boundary

Status: NOT_RUN

Attempt approval against an obsolete submission digest and receive a conflict.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-009 — Compensation and reconciliation • expected behavior

Status: NOT_RUN

Approve the 20.00 deliverable twice and find one payable.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-010 — Compensation and reconciliation • failure boundary

Status: NOT_RUN

Terminate the worker after dispatch, then restart and prove that the unknown intent cannot dispatch a second payout.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-011 — Client reporting and privacy • expected behavior

Status: NOT_RUN

A client reads the redacted accessibility observation referencing release r18.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-012 — Client reporting and privacy • failure boundary

Status: NOT_RUN

Use a different client identity to fetch the attachment and verify access is denied.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-013 — Retest and release comparison • expected behavior

Status: NOT_RUN

Open r19 and see an untested linked commission next to the r18 failed observation.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-014 — Retest and release comparison • failure boundary

Status: NOT_RUN

Compare reports with different assistive technology and show the comparison caveat.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-015 — End-to-end agency pilot • expected behavior

Status: NOT_RUN

Verify a real reader observation and both outcome fields through the ordinary UI.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.

### T-016 — End-to-end agency pilot • failure boundary

Status: NOT_RUN

Disconnect the payout adapter and show the payable pending while accepted findings remain available.

Record the initial persisted state, actor, observable result and final persisted state. Attach the test command and test report to the implementation handoff.
