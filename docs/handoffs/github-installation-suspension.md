# Installation suspension handling

The dedicated `/installation-suspension` receiver authenticates original bytes and requires a
signed `suspend` action, matching numeric App/installation identity and a valid UTC
`installation.suspended_at` timestamp. It revokes only its operator-configured local binding
using the same transaction as receipt/delivery deduplication. Unsuspend never reauthorizes
that binding; a fresh owner-authorized access probe and binding are required.

This receiver is deliberately scoped to one workspace/binding, not a global installation
broadcast. Every affected deployment must receive/routinely reconcile its own access loss.
Installation deletion, transfer, automatic routing/fan-out and fresh remote reconciliation
remain separate work. An old signed denial may conservatively revoke a binding; there is no
claim of current remote status or ordering of GitHub deliveries.

Each endpoint is now explicitly bound to its operation, rather than comparing the request's
full URL path. This preserves denial handling when the receiver is mounted under a prefix.
All endpoints share body-size/deadline/header guards. No deployed configuration was changed.

Focused ingress checks cover all three endpoints, direct/mounted paths, and stalled/complete
bodies. Synthetic signed PostgreSQL integration cases cover suspension/replay, rejected
unsuspend and malformed/foreign installation identities. Neither class of check is a live
GitHub delivery or production deployment attestation.
