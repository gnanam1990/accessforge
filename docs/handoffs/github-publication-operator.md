# Explicit GitHub publication operator

The trusted orchestrator host can now invoke the existing approved-preview publisher directly:

```sh
python -m accessforge_orchestrator.github_operator \
  --scope-file /canonical/private/publication.json \
  --key-file /canonical/private/github-app.pem \
  --allow-github-publication
```

This is an intentionally remote-writing command, not a diagnostic or automatic queue consumer.
The flag authorizes temporary installation-token issuance and a single approved check-create
attempt. Omitting it refuses before file access. Importing the module performs no work.
Do not run it in a repository build, desktop navigator, public API worker or untrusted account.
Provision a dedicated trusted OS account/service boundary; this code does not create one.

The closed JSON object contains exactly `schemaVersion` (integer 1), `appId` (positive integer),
`workspaceId`, `userId`, `sessionId`, `previewId`, `approvalId` (canonical UUID strings), and
`previewDigest` (64 lowercase hexadecimal characters). These are references to the original
authenticated owner/session and separately reviewed/approved preview, not login credentials or
permission to fabricate approval. The command rechecks current application authority and exact
App/preview identity before reading the key; the publisher independently rechecks current scope,
approval, retained bytes and its one-shot durable reservation before dispatch.

Both paths must be canonical absolute POSIX paths to private, current-user-owned regular files
in private owned directories. Symlinks, hardlinks, group/other permissions and changing files are
refused. Config is bounded at 8 KiB and PEM at 16 KiB. The existing offline signer validates the
RSA key and signs its fixed short-lived App JWT. No key generation, discovery, chmod, credential
serialization or navigator disclosure occurs. These cooperative filesystem checks do not defend
against root or malicious code sharing the OS account; Python does not guarantee zeroization.

The operator supplies `ACCESSFORGE_DATABASE_URL` and the existing evidence-store environment
configuration independently. No credentials belong in the JSON, command arguments or repository.
The command prints only historical creation receipt IDs on success. Before publisher entry,
failure is `GITHUB_PUBLICATION_REFUSED`. After entry, failure is
`GITHUB_PUBLICATION_UNCONFIRMED`: inspect the original preview's recovery endpoint; never create
a new preview/run or retry merely to bypass an existing reservation. Success does not prove
current remote existence/contents. SIGINT can interrupt cleanup; SIGTERM/process death can bypass
cleanup entirely. Missing terminal output is unconfirmed, not proof that nothing happened or
that the temporary token was revoked. Reconcile the original intent and installation credential
through the operator's incident procedure; do not redispatch.

Validation: 14 focused filesystem/closed-schema/explicit-flag/authority/wiring/error-redaction
checks passed with synthetic publisher/signing/database ports. Ruff and strict mypy passed.
The existing publisher's real DB/S3 and mock GitHub checks are separate service evidence.
No real App key was loaded, token issued or check created during implementation. Actual isolated
host provisioning, authorized App delivery and uncertain-outcome rehearsal remain outstanding.
