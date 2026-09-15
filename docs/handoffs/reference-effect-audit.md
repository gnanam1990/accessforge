# Protected reference creation history (C2 source component)

`accessforge_persistence.evidence.effect_audit` provides an explicit, one-shot administrative
installation and an independent read path. It is not invoked by application startup, migrations,
the navigator, or the canonical finalizer. No live application database was changed to ship it.

An AFTER INSERT, ENABLE ALWAYS trigger records committed service-request insertions into a
separate durable table without a cascading foreign key. A committed create-then-delete remains
visible; a rolled-back insertion does not. A final service-request row count is not this history.

## Provisioning contract

- Supply an empty reference request table, two existing independent unprivileged roles, and a
  caller-owned administrative transaction. The installer creates no credentials or roles.
- The installer transfers ownership of `fixture_instance` and `service_request` to the current
  administrative role and replaces direct application/observer grants on those tables. Review
  this ownership/ACL change before using it on an existing deployment. Do not use the application
  or observer as the administrator. The administrator remains explicitly trusted.
- Existing requests are refused, never reset. An existing audit schema is refused, never replaced.
  A savepoint rolls back partial provisioning even when the caller catches validation failure.
- Commit successfully before retaining/using the returned installation UUID. Reconcile an
  uncertain commit against the original installation; do not blindly reinstall.
- Use a fresh explicit observer transaction for reads, binding the expected application role,
  installation UUID, and fixture nonce. Reads validate source/trigger identity, durability, role
  authority, and table/column privileges before returning a history count. SQL/validation failure
  is unavailable evidence, never zero. Do not grant the observer source or audit write authority.
- Reads require the installed observer's effective `current_user`; an administrator connection
  cannot substitute for that role. PostgreSQL read errors are normalized to `AuditUnavailable`
  with their original cause retained for private diagnostics.

The trigger function has a fixed trusted search path and PUBLIC execution revoked, following
[PostgreSQL's SECURITY DEFINER guidance](https://www.postgresql.org/docs/16/sql-createfunction.html).
Accessible non-system security-definer functions are conservatively refused rather than assumed
safe. This is a dedicated reference-database boundary, not a general database capability sandbox.

## Evidence and remaining work

Focused real PostgreSQL integration checks use newly generated disposable databases and NOLOGIN
roles. They exercise committed deletion/rollback, actual denied writes and trigger bypasses,
changed identities/privileges/RLS, duplicate/nonempty installation refusal, and caught-failure
rollback. These are database integration checks, not a screen-reader journey or production proof.

The returned count spans committed insertions since installation. It has no authenticated
run/attempt window, monotonic clock epoch, retained receipt, or canonical assertion. Zero MUST NOT
be converted into `CONTINUOUS_EFFECT_ABSENCE`. C2 still needs independent window collection,
source authentication/retention, and finalizer integration; other forbidden effects are not
measured by this service-request-specific source. Administrator tampering outside a read snapshot
is not ruled out by catalog inspection. C1 runtime configuration and actual acceptance remain open.
