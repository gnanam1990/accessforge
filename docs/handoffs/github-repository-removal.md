# Explicit repository-removal ingress

The separately hosted receiver now exposes `/repository-removal` as a deny-only endpoint.
It authenticates the original bounded body before inspecting a signed `removed` action,
installation/App IDs and a nonempty `repositories_removed` list. Only the operator-configured
workspace/binding can be revoked, and its repository must be explicitly listed. The unsigned
event header cannot authorize this operation. The original `/webhook` stays receipt-only.

Receipt/delivery deduplication and local revocation commit in one transaction. A duplicate
removal can return the same successful disposition even after revocation. A conflicting
delivery identity rolls back without revoking. Nothing reinstates a binding or grants remote
publication authority. The response describes local revocation, not remote App uninstallation.

This endpoint needs deliberate ingress routing and deployment. It does not replace handling
of installation deletion/suspension, transfers or selection-mode changes with empty removal
lists. Those remain pending; outbound operations must still freshly verify remote access.
No App deployment or live webhook delivery was performed to implement this change.

GitHub event reference: [webhook payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads#installation_repositories).
The added integration cases cover explicit removal/replay, wrong scope, unsupported actions,
empty lists and invalid repository IDs using synthetic signed input and real PostgreSQL in CI.
