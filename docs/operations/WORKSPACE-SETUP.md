# Operator workspace setup

`scripts/provision_workspace.py` creates a **new** local user, a **new** workspace,
and OWNER membership in one audited transaction. Run it only after independently
verifying who should own the workspace and obtaining authorization. This is not
public signup, an identity proof, or a grant to an existing account.

Use an operator database connection in `ACCESSFORGE_DATABASE_URL`, never a DSN in
command arguments or source control. The existing schema must already be migrated;
this command does not migrate, start services, or change database role permissions.
Choose and retain new UUIDs before invoking:

```sh
uv run python scripts/provision_workspace.py \
  --user-id NEW_USER_UUID --workspace-id NEW_WORKSPACE_UUID \
  --email OWNER_CONTACT_EMAIL --name 'Workspace name' \
  --operator AUDIT_LABEL --confirm-create
```

The operator label is attribution, not authorization. Email is contact metadata,
not proof of identity. Existing IDs or an exact email conflict cause refusal;
there is no upsert, recovery, reactivation, or automatic account linking. An audit
failure rolls back setup. A connection/commit error can have an unknown outcome:
inspect the retained IDs and both audit events before retrying. Do not generate new
IDs and blindly repeat an unconfirmed operation.

Then use the separate [GitHub identity binding](../handoffs/github-user-login.md)
command for the independently verified numeric GitHub subject and this user UUID.
Provisioning issues no login session and creates no runner, entitlement, model
credential, or execution approval. Verify actual browser login separately.
