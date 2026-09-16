# Invited account signup

An anonymous visitor following a canonical invitation link can explicitly submit a new-account
form. `POST /v1/auth/github/start` accepts only a bounded URL-encoded invitation pair, contact email
and `createAccount=yes`, with a matching HTTPS Origin. Ordinary GET login remains existing-account
only. Both entry points share durable global challenge admission and PKCE/browser-state handling.

Email is unverified contact metadata, never an account-linking credential. It is sent in the POST
body, not the URL, provider authorization request or return address. The temporary Secure/HttpOnly
five-minute cookie carries its encoding; the durable challenge configuration hash binds it and
the invitation. The cookie encoding is not encryption; it must remain private like login state.
Raw contact data is not included in audit details or object representations.

Only after challenge consumption and fresh GitHub identity exchange may an unbound subject create
a local account. The exact active offer and its current enabled OWNER issuer are locked and
checked. Revoked bindings, wrong subjects, expired/revoked offers and case-insensitive email
collisions are refused without overwriting another account. New UUID, immutable identity binding,
audit and session issuance commit together. Existing enabled bindings keep their original account
and metadata. A failed or lost login is never automatically replayed.

Signup grants no membership and does not consume the offer. The signed-in user must separately
read and explicitly accept the invitation through the subject-bound recipient API. No email is
sent. There is no open/public workspace creation flow and no automatic account linking by email.

This change adds no migration beyond the invitation stack's required 0074. It has not been deployed
or exercised with a real provider. Focused static/parser/UI evidence and CI are separate from
actual signup and full reader/model acceptance. Required CI includes real PostgreSQL HTTP cases
for fresh signup without membership, wrong subject, expiry, revocation and email collision.
