"""Workspace allowlist storage for a trusted integration service, not public request data.

Creation requires an operator-authorized workspace and a just-completed App access probe.
Neither the stored observation nor a live binding substitutes for fresh remote access checks,
current user authorization, or exact payload-bound publication approval.
"""

from typing import Any
from uuid import uuid4

import psycopg


class Refused(ValueError):
    pass


def record_verified(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    app_id: int,
    installation_id: int,
    account_id: int,
    repository_id: int,
    owner: str,
    name: str,
    observed_at: str,
) -> str:
    """Retain one exact allowlist identity; never overwrite an existing live binding.

    Only a trusted caller may supply the probe result. The DB enforces original observation
    freshness against its own clock. This function does not authenticate a user or GitHub.
    A failed outer transaction does not create a binding; there is no remote retry here.
    """
    binding_id = str(uuid4())
    with conn.transaction():
        conn.execute(
            "INSERT INTO github_repository_binding"
            "(id,workspace_id,app_id,installation_id,account_id,repository_id,"
            "owner_name,repository_name,observed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                binding_id,
                workspace_id,
                app_id,
                installation_id,
                account_id,
                repository_id,
                owner,
                name,
                observed_at,
            ),
        )
    return binding_id


def require_live(
    conn: psycopg.Connection[Any], *, workspace_id: str, binding_id: str
) -> dict[str, Any]:
    """Lock the current local allowlist until caller commit; not an outbound write grant.

    Revocation waits for this transaction, so callers must keep it short and must not hold
    it during remote I/O. Recheck after any remote observation before admitting local work.
    GitHub-side removal/transfer is independent and requires a fresh remote scope check.
    """
    row = conn.execute(
        "SELECT * FROM github_repository_binding WHERE id=%s AND workspace_id=%s "
        "AND revoked_at IS NULL FOR UPDATE",
        (binding_id, workspace_id),
    ).fetchone()
    if row is None:
        raise Refused("repository binding unavailable")
    return dict(row)


def disconnect(conn: psycopg.Connection[Any], *, workspace_id: str, binding_id: str) -> bool:
    """Irreversibly revoke locally; does not uninstall an App or undo remote operations."""
    return (
        conn.execute(
            "UPDATE github_repository_binding SET revoked_at=clock_timestamp() "
            "WHERE id=%s AND workspace_id=%s AND revoked_at IS NULL",
            (binding_id, workspace_id),
        ).rowcount
        == 1
    )
