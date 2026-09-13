# Retained runtime artifact build receipts

The trusted regression coordinator now persists each fresh deployed-artifact
measurement before returning it to a controller or allowing gateway response
delivery. The observing worker must still own the exact dispatched regression
epoch and current patch/build authority, with the original live endpoint. Wrong
artifact/container/image/daemon identities and stale or pre-binding timestamps
refuse. No public API accepts purported measurement JSON.

Migration0045 adds immutable workspace-isolated `candidate_artifact_observation`
receipts. Their digests bind the original measurement, build, regression epoch,
runtime policy and endpoint binding. Optional run/first-reader-lease context is
captured only after validating that binding; a binding created after the sample
cannot be applied retrospectively. Preview measurements have no run/lease identity.

The parent worker lock serializes the bounded256-receipt lifetime. An identical
measurement and context returns the same deterministic receipt identity. Source
text, fixture values, worker tokens and database/provider credentials are absent.
Updates/deletes refuse except parent cleanup; receipts survive normal endpoint
closure as historical metadata. Internal reads recheck digest and row/context
identity and respect workspace RLS. They do not renew execution authority.

A lost persistence acknowledgement or exceeded deadline stops the gateway without
replaying a possible POST effect. This migration is not applied to user data here.

## Evidence separation

These are BUILD receipts, not canonical SUPERVISOR/OBSERVER events. No event ACL is
expanded and no source/model/environment identity or PASS/VERIFIED verdict is
inferred. Canonical runtime evidence still needs the supervisor/evaluator bridge,
with actual reader and trusted deployment proof. Filesystem samples do not attest
process memory or exclude change-and-restore between observations.

Scoped static checks run locally; existing isolated CI fixtures exercise receipt
binding, workspace isolation, immutability and history after cleanup. The forward
migration ledger includes0045. No local full suites, real reader, provider call,
deployment or OS settings changes were performed.
