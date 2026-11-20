# AccessForge Tester Desk · Technical design

## Architecture and ownership

Preserve an established target repo's stack and conventions. For a greenfield target the proposed baseline is a typed HTTP application, PostgreSQL, a supervised background worker and a role-aware web console. Use object storage for large evidence, opaque references in the database, and server-side authorization on retrieval. A transaction writes a domain decision and its outbox row together. A worker processes external side effects and stores a durable result independently of the browser session.

**Actors.** An agency lead commissions a test and accepts an agreed evidence deliverable. A consenting independent tester selects an eligible assignment, performs the task and controls which evidence they submit. A finance operator reconciles compensation. The client sees only the authorized summary.

## Domain schema

Commission(id, agency_id, client_id, release_digest, task_steps, environment, acceptance_rubric, fee_minor, currency, deadline); Assignment(id, commission_id, tester_id, consent_revision, consent_at, expires_at); Observation(assignment_id, step_id, observed_text, result, evidence_hash); Submission(id, assignment_id, revision, manifest_hash, submitted_at); Acceptance(submission_id, reviewer_id, deliverable_decision, reason); Payable(assignment_id UNIQUE, accepted_revision, payee_profile, amount, payout_intent_id UNIQUE, external_ref, state). Isolate tester identity from client exports. BriefRevision(id,commission_id,release_digest,task_steps,environment,rubric,fee_minor,currency,consent_terms_version,brief_hash) is immutable. Assignment holds brief_revision_id as a required foreign key. Price and scope read from that revision for all compensation decisions.

Index tenant-scoped business keys, workflow status and pending work. Version migrations and test forward migration from a populated previous schema. Write quantities and currency using explicit units and integer minor or token units. Persist the precision alongside provider amounts. A comparison involving different currencies requires an explicit valuation source and timestamp.

## HTTP and worker boundaries

POST /commissions signs a versioned brief. POST /commissions/{id}/accept atomically claims it. PUT /assignments/{id}/observations/{step} saves a draft. POST /assignments/{id}/submit seals an evidence revision. POST /submissions/{id}/review binds acceptance or a revision request to that revision. GET /commissions/{id}/report renders only client-readable fields. POST /payables/{id}/reconcile records an authorized provider status lookup.

Apply tenant membership and action scope on the server. Use a durable idempotency key on commands that can trigger work or payment. An identical retry returns the stored operation reference. A different payload under the same key yields a conflict. Keep polling and webhooks causally tied to the original operation. Apply provider signature verification when supported and reject unverified callbacks as evidence of settlement.

## Decision procedure

Hash the release, task and acceptance rubric into one brief version. Match exact technology requirements, prevent conflicting claims and require current consent. Validate required step outcomes and permitted attachments. Keep the tested task outcome (passed, failed, inconclusive) independent from deliverable acceptance. A complete failure observation can earn the full posted fee. Create one payable in the same transaction as acceptance and match the provider payment reference before marking it settled.

## State model

Commission draft → open → assigned → submitted → in_review → accepted or needs_revision. A new submission advances the revision number. Open offers may expire. Submitted reports keep task_result independently. Payable eligible → processing → settled or unknown; definitive rejection is reviewable. Consent withdrawal closes further collection and routes retained evidence through the stated data policy.

## Recovery

Autosave observations under the assignment version. If an upload fails, retry attachment by content digest. Lock payable issuance to the accepted submission. A provider timeout leaves one unknown intent for finance reconciliation. A revision changes the accepted evidence hash and requires a fresh review before issuance.

Use a compare-and-set version or row lock for conflicting decisions. Persist an intent before dispatch. If the provider cannot establish what happened, expose a pending-reconciliation state and stop automatic redispatch. Reconcile with the original provider reference, exact amount and scope. Maintain append-only corrections where users depend on the original evidence.

## Operations and integration

Record operation ID, actor role, decision code, latency and the external reference in access-controlled logs. Track stalled operations by age and surface them to the responsible role. Run migrations before workers, test a process restart during an in-flight request, and check convergence from an independently restored database. Deploy web and worker with separate credentials. See [the integration contract](KITE-AND-PROVIDERS.md) and [the tests](TEST-PLAN.md).
