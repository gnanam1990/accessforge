# Single-URL GitHub ingress

An operator may configure the separately hosted receiver's `/events` URL for its one exact
workspace/repository binding. Original body authentication precedes dispatch selection:
explicit removed-repository bodies reach deny-only removal, signed suspension bodies reach
deny-only suspension, and other bodies reach the existing exact-repository receipt handler.
Each selected handler also authenticates and checks its own full scope. No event-header value
can select or grant authority. Unknown installation-only events still refuse.

`ACCEPTED_LOCAL_EVENT` means a local authenticated receipt or local access revocation was
committed; it is not an agent job, Check Run, or remote access attestation. `/webhook` remains
receipt-only for compatibility, and the dedicated denial routes remain available. All routes
retain bounded body reads, static errors and durable delivery/body deduplication.

The service has not been deployed. Multi-workspace fan-out, installation deletion/transfer,
selection-mode reconciliation and outbound publication remain separate work. Failed or
unsupported ingress cannot replace fresh remote access checks before outbound operations.

Focused checks verify real HMAC before handler selection without a database. The added
PostgreSQL integration scenario sends a repository event and a removal to the same URL and
checks that later ordinary receipt attempts are denied without starting a run.
