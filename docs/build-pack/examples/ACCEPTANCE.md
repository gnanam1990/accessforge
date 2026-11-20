# AccessForge Tester Desk · Demonstration script

**Input and expected outcome.** Commission AF-21 pays 20.00 USD for a five-step checkout on release r18. Step four traps keyboard focus. Tester marks step four failed and step five unreachable, submits the agreed evidence, reviewer accepts it. Expect task failed, evidence accepted and one 20.00 payable. Repeat the submit and approval after restart: still one payable. Open r19 and show a new pending retest with no inherited pass.

**Operator surfaces.** Agency brief editor, tester's screen-reader-friendly assignment inbox, step-by-step observation form with autosave and explicit upload consent, reviewer side-by-side rubric and evidence, client redacted report and finance queue. Announce draft save and submission without moving focus; preserve navigation across validation errors.

**Failure to demonstrate.** Autosave observations under the assignment version. If an upload fails, retry attachment by content digest. Lock payable issuance to the accepted submission. A provider timeout leaves one unknown intent for finance reconciliation. A revision changes the accepted evidence hash and requires a fresh review before issuance.

Create a labeled, rights-cleared sample through the import or creation UI. Identify the seeded input separately from all real actions and receipts. Record with two appropriate role sessions where required, browser refresh and worker restart. Use a final export or read-only case view tied to the input revision; show any unsupported capabilities as such. Record actual outcomes next to expected outcomes in the target repo.
