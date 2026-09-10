# `packages/clients`

The Python client, the operator CLI, and the generated operation table both languages share.

| Path | |
|---|---|
| `python/src/accessforge_client/_operations.py` | **Generated** by `scripts/generate_clients.py` from `contracts/openapi.json`, which is itself generated from the live application. Do not edit. |
| `python/src/accessforge_client/client.py` | Written by hand. How to talk to this API *safely* is not something a contract describes. |
| `python/src/accessforge_client/cli.py` | The `accessforge` command. |
| `ts/src/operations.ts` | **Generated**, same source. The web client is hand-written in `apps/web`; this exists so it can assert every URL it builds is one the API serves. |

## What is generated and what is not

**Generated:** the operation table — method, path, path parameters, whether it mutates. That is the
part that drifts silently and that nobody can check by reading.

**Not generated: the transport.** Cookies, CSRF, `If-Match`, `Idempotency-Key`, and what a problem
document means are decisions about how to use this API safely. A contract does not describe them, so
generating them would produce a confident client that gets them wrong.

**Not generated: response types.** Every route returns `dict[str, Any]`. A type-generating client
would emit `Any` for every field and imply a precision that does not exist — telling you
`run.outcome` is a `RunOutcome` when the server promises no such thing.

## The two rules this client keeps

**A 202 is a `Requested`, never a result.** It has no truthiness anybody would misread and no field
a caller could mistake for a verdict. `if client.call("request_run", ...)` cannot be written to mean
"the run happened".

**It cannot bypass server policy.** Every check — membership, permission, revision, quota,
idempotency — happens on the server, and this client reaches the same routes a browser does. There is
no `--force` and no local validation that skips a round trip: a client-side rule that disagreed with
the server would be a second, wrong definition, and the wrong one is always the one somebody trusts.

## Using it

```bash
uv run accessforge sign-in --email you@example.test
uv run accessforge project list --workspace WS
uv run accessforge run request --workspace WS --project P --manifest DIGEST
uv run accessforge operations          # what this build can call
uv run accessforge export verify BUNDLE.zip --key trust-root.json
```

`export verify` delegates to module 17's offline verifier rather than reimplementing it. That
verifier's whole value is that a reader can check a bundle with no access to this system; a CLI that
verified by calling the API would destroy that property while appearing to keep it.

## Regenerating

```bash
uv run python scripts/generate_clients.py           # after any route change
uv run python scripts/generate_clients.py --check   # what CI runs
```
