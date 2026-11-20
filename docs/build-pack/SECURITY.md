# AccessForge Tester Desk · Threat model

Keep private test credentials out of evidence, strip metadata, scope artifacts by role and assignment, expire links, detect double claims, and bind review to release and submission digest. Limit tester disclosure to opted-in artifacts. Conflicts of interest are recorded declarations; a wallet alone cannot establish independence.

The web client, uploaded records and provider payloads are untrusted. Enforce organization, ownership and action-specific authorization at read and write boundaries. Test guessed IDs, stale approvals and access to evidence after membership revocation. Bound extraction payload sizes, validate data formats, redact secret-bearing query strings and keep private data out of public endpoints. Network clients restrict target schemes and destinations to configured providers. Keep customer data, tokens and payment details out of prompts and handoff logs.

Bind an operation to the actor, payload digest, amount limit, provider and expiry where applicable. Audit original and corrected decisions with actor and reason. Include redacted reproduction steps for a failed or unknown result. Align the retention policy with data rights agreed in the pilot.
