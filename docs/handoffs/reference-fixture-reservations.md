# Reference fixture reservations

The setup-only fixture endpoint accepts an optional bounded `nonce` query parameter. A trusted
controller can persist a fresh per-run nonce before requesting app provisioning, then reconcile an
uncertain response using that same identity. No nonce supplied preserves server-generated creation.

New instances return 201. An exact existing template/variant with no service requests returns 200
without changing it. A conflicting or used instance returns 409. Replay takes a row lock and never
deletes a fixture, service request or another run's evidence. Setup authentication remains required;
observer credentials cannot provision fixtures. The global reset endpoint is not called by this path.

This endpoint is not a run-authorization gate, reset attestation or proof of physical execution.
The trusted controller still needs to bind the nonce to the sealed run before the call, retain its
result, independently verify empty application state and revalidate authority before browser actions.
The existing browser setup helper is not yet wired to this reserved-nonce flow.

Changed-file Ruff and strict mypy passed. CI integration cases cover replay, mismatched variant,
used nonce, other-run preservation, missing/wrong setup authority and malformed nonce. No live app
provisioning, migration, AT startup or deployment was performed.
