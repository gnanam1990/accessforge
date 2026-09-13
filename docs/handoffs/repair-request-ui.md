# Browser repair request and recovery

The finding screen can select a retained, non-superseded SOURCE_LINKED diagnosis
and read the server's current repair disclosure options. The backend remains
authoritative about source, diagnosis, predecessor and requester eligibility.

Native labelled controls show complete allowed file paths, provider configuration,
exact scope identities and any separately reviewed dependency/build paths. Both
disclosure/billable consent and (when needed) separate-scope acknowledgement begin
unchecked. Recording consent does not invoke the model or dispatch a reader.

An opaque `repairOperation` query parameter is retained before POST. Lost responses,
reloads and remounts recover with GET only. Unknown/missing/foreign responses do not
offer a replacement request. STARTED and UNCONFIRMED reservations cannot be bypassed
in this UI, even after revocation. A successor requires a fresh server preview and
fresh acknowledgements; the backend rechecks lineage at insertion.

Readback separates invocation settlement, expiry/revocation and delivery receipts.
A matching PROPOSED receipt links to the existing patch review screen, without
claiming approval, application or verification. Missing receipts are not interpreted
as zero provider use. The original requester can explicitly revoke and read back;
this cannot undo disclosure or establish that already-entered work stopped.

Session/role/finding changes reset consent and abort local request controllers.
Aborting transport never proves server rollback. Source/model text is inert React
text, with exact paths displayed as JSON strings. Native controls preserve tab
order and existing focus styling; no visual redesign or additional dependency.

Validation: production web build/typecheck and whitespace checks locally. Focused
synthetic fixtures are authored for existing CI only. No local full test suite,
real provider call, browser/assistive-technology acceptance, OS changes, deployment
or real source disclosure was performed. This UI is not production/native proof.
