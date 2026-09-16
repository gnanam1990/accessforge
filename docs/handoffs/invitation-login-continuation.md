# Invitation-aware login continuation

The shareable entry point is `/workspaces?invitationWorkspace=<workspace-uuid>&invitationId=<invitation-uuid>`.
Both references must be canonical lowercase UUIDs. They are not secrets, identity claims,
authorization or proof that an offer exists. No arbitrary return URL is accepted.

The signed-out provider gate carries a valid pair into same-origin GitHub login navigation.
The server binds the pair into the durable challenge's configuration hash and stores the references
with the temporary browser secrets in the existing Secure, HttpOnly login cookie. GitHub receives
only the existing OAuth state/PKCE data, not the invitation references. Adding, removing or changing
the pair breaks challenge consumption before provider exchange. Ordinary login challenge hashes
remain compatible; no migration is introduced.

After successful existing-account authentication, the server redirects to the fixed workspace
chooser with the bound pair. The chooser prefills reference inputs only. The recipient must still
read the server's subject-bound offer, confirm its role, and explicitly accept it. No automatic
membership action occurs. Invalid/duplicate/extra query fields do not become login redirects.

This is **not new-account signup**. An immutable, enabled GitHub-to-local-account binding must
already exist. Invitation-aware provisioning, contact-metadata capture and owner-facing link
sharing remain separate work. A valid reference for somebody else's offer does not expose it;
the recipient API's verified-subject check remains authoritative.

Local evidence: 9 focused Python continuation checks, Ruff and mypy passed; 43 focused frontend
reference/invitation/shell checks passed. A real-PostgreSQL ASGI regression for tampered context,
unconsumed valid recovery and membership-free login is committed for required CI, not locally run.
No real provider login, account creation, membership grant, deployment or AT operation was performed.
