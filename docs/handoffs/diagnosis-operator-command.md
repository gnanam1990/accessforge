# Explicit diagnosis operator command

Depends on the authenticated request workflow in PR 44. This is a one-shot operator entry point,
not a daemon, automatic queue consumer, desktop process or a request made during development.

## Operator prerequisites

An authorized human first reviews the profile and creates an unexpired diagnosis request through
the authenticated API. On the trusted orchestrator host, an operator separately provisions a
canonical absolute JSON file in an existing private directory, both owned by the current POSIX
user. The file must be a regular non-symlink file, at most 32 KiB, without hardlinks or group/other
permission bits. The loader reads only; it never creates, overwrites or chmods operator files.

The closed file schema has these fields:

| Field | Required value |
| --- | --- |
| `schemaVersion` | Integer `1` |
| `workspaceId` | Exact authenticated request workspace UUID |
| `requestId` | Exact original request UUID, not a new retry identity |
| `sourceRoot` | Canonical absolute path of the independently authorized frozen checkout |
| `sourceCommitSha` | Original sealed 40-character lowercase commit hash |
| `allowedFileDigests` | Object mapping 1–20 relative source paths to exact 64-character file hashes |

The source reader retains its 20-file, 250,000-byte and 200-lines-per-excerpt defaults. It checks
clean commit/tree identity and individual file hashes independently against the sealed run before
provider disclosure. A path in a request does not widen this host allowlist. Secret paths, traversal
and symlink escapes still fail at the source-reader boundary. This cooperative file boundary does
not defend against root or a malicious process using the same OS account.

## Explicit dispatch — potentially billable

Only after the operator is authorized to disclose the reviewed evidence/excerpts and incur provider
costs, replace the placeholder arguments and invoke:

```sh
uv run python -m accessforge_orchestrator.diagnosis.operator \
  --workspace-id WORKSPACE_UUID \
  --request-id ORIGINAL_REQUEST_UUID \
  --scope-file /canonical/private/diagnosis-scope.json \
  --allow-billable-model-call
```

The host supplies `ACCESSFORGE_DATABASE_URL`, the existing `ACCESSFORGE_EVIDENCE_*` settings and the
provider credential chain. These are never fields in the provisioning file or HTTP request. The
command never prints raw source, hypotheses, credential-bearing errors or provider responses.
Omitting the billable-call flag refuses before reading any file or invoking a worker.

SIGINT/SIGTERM set the worker cancellation fence. Success prints only retained finding/diagnosis
IDs; it does not claim a verified cause or successful repair. A failure before worker entry reports
DISPATCH_REFUSED. Once worker entry is possible, failure reports UNCONFIRMED: inspect the original
request/invocation through the read API, not a new operation ID. Durable STARTED/UNCONFIRMED records
prevent blind redispatch. A signal is not proof that a provider has stopped or that nothing was billed.

Local validation: changed-file Ruff/mypy and diff checks. Private-file and explicit-flag cases are
authored for CI only. No command dispatch, real source provisioning, provider call, reader run or
deployment occurred while implementing this slice. Real host provisioning, browser request UI,
provider usage reconciliation and actual source/model/reader acceptance remain pending.
