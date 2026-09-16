"""Explicit host-operator provisioning, never called by a public signup route.

Creates only a new local user and a new workspace. No identity binding, session,
entitlement, runner enrollment or execution approval is implied by provisioning.
Database access is the authority; the operator label is audit attribution only.
"""

from __future__ import annotations

import re
import uuid

import psycopg

from accessforge_persistence import unscoped_connection

from .membership import record_audit_event, record_global_audit_event


class WorkspaceSetupRefused(Exception):
    """Setup refused; do not infer success or retry an unknown commit outcome."""


def provision_workspace(
    database_url: str,
    *,
    user_id: str,
    workspace_id: str,
    email: str,
    name: str,
    operator: str,
) -> None:
    """Create both records and OWNER membership in one audited transaction.

    IDs must be chosen and retained before invocation. Existing IDs or emails
    cause refusal, never an upsert, reactivation or grant to an existing account.
    Email is contact metadata, not proof of identity or an account-linking key.
    """
    try:
        user, workspace = str(uuid.UUID(user_id)), str(uuid.UUID(workspace_id))
    except ValueError:
        raise WorkspaceSetupRefused("invalid setup arguments") from None
    if (
        user != user_id
        or workspace != workspace_id
        or not re.fullmatch(r"[^\s@\x00-\x1f\x7f]+@[^\s@\x00-\x1f\x7f]+", email)
        or len(email) > 254
        or not 1 <= len(name) <= 120
        or name != name.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", operator)
    ):
        raise WorkspaceSetupRefused("invalid setup arguments")
    actor = "workspace-operator:" + operator
    try:
        with unscoped_connection(database_url) as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            # Local-development login compares lower(email). Serialize this
            # operator's case-variant creates using the same database folding.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(lower(%s), 0))", (email,))
            if (
                conn.execute(
                    "SELECT 1 FROM app_user WHERE lower(email)=lower(%s)", (email,)
                ).fetchone()
                is not None
            ):
                raise WorkspaceSetupRefused("setup conflicts with existing records")
            conn.execute("INSERT INTO app_user(id,email) VALUES(%s,%s)", (user, email))
            conn.execute("INSERT INTO workspace(id,name) VALUES(%s,%s)", (workspace, name))
            record_global_audit_event(
                conn,
                action="operator.user_created",
                target_kind="app_user",
                target_id=user,
                outcome="ALLOWED",
                actor_service=actor,
            )
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, true)", (workspace,))
            conn.execute(
                "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES(%s,%s,'OWNER')",
                (workspace, user),
            )
            record_audit_event(
                conn,
                workspace_id=workspace,
                action="operator.workspace_created",
                target_kind="workspace",
                target_id=workspace,
                outcome="ALLOWED",
                actor_service=actor,
                detail={"ownerUserId": user},
            )
    except psycopg.IntegrityError:
        raise WorkspaceSetupRefused("setup conflicts with existing records") from None
