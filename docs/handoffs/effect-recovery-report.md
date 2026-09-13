# Form-transport recovery inspection

`GET /v1/workspaces/{workspace}/runs/{run}/effect-deliveries` is a no-store, evidence-read protected
history page, available after the original machine session has expired. Forced workspace RLS and
one SQL statement snapshot keep the returned run, permission, action, lease and transport rows
aligned. Original grant digests and action bindings are checked. Corrupt history is refused rather
than reconstructed from a seal. Pages contain at most 100 records and an explicit next cursor.
Cursor ordering is by immutable permit UUID, not chronology; action sequence is reported separately.

The CLI provides the same report in an explicitly read-only, workspace-scoped transaction:

```sh
uv run accessforge-effect-recovery --workspace-id WORKSPACE_UUID --run-id RUN_UUID
```

It reads `ACCESSFORGE_DATABASE_URL` from the operator environment. It never prints that URL, invokes
a model, probes a reader/application, sends HTTP effects, changes a result, releases a lease, or
rearms permission. A `nextCursor` can be supplied as `--after`; no implicit unbounded scan occurs.

Report phases distinguish OPEN, CLOSED_UNUSED, UNCONFIRMED, DELIVERY_RECORD_MISSING and
RESPONSE_RETAINED. The last means a response status/digest was retained, not that the requested form
effect or journey succeeded. Missing delivery history remains visible; empty history is not proof
of zero application effects. Recorded ambiguity still requires investigation even with a response.
Private form paths, HTML, fixture values, machine credentials and grant payloads are not returned.

Use the original action/attempt/lease identities to inspect independent observer evidence and the
supervisor journal. Any physical stop/reset still requires its existing authorization and proof;
this report expressly grants neither retry nor reset authority. It does not reconcile an unknown
effect by declaration or rewrite its original action result.

Local validation: scoped Ruff/mypy, generated OpenAPI/clients, CLI help, and diff checks. Focused
synthetic projection/pagination cases and the existing real-PostgreSQL finalized-run/foreign-workspace
case run in CI. No local full tests, live database reads/migrations, reader or paid provider calls.
