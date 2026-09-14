"""Trusted connection service: live local authorization, exact remote probe, durable binding.

The caller authenticates the human/session and loads the App JWT from isolated operator
configuration. None of these arguments is a public request-body credential. No HTTP route or
credential broker is enabled by importing this module. Connecting never approves publication.
"""

from typing import Any

import httpx
import psycopg

from accessforge_domain.authorization import HumanPrincipal, Permission, Role
from accessforge_domain.authorization.roles import role_permits
from accessforge_persistence import github_bindings, workspace_connection

from .github_access import RepositoryScope, inspect_repository


class Refused(ValueError):
    pass


def _authorize(conn: psycopg.Connection[Any], principal: HumanPrincipal) -> None:
    # Use current database identity, not the role cached on the incoming principal. Locks
    # serialize local revocation against the final insert, but never span the HTTP probe.
    row = conn.execute(
        "SELECT m.role FROM workspace_membership m "
        "JOIN app_user u ON u.id=m.user_id "
        "JOIN user_session s ON s.user_id=u.id "
        "WHERE m.workspace_id=%s AND m.user_id=%s AND s.id=%s "
        "AND m.revoked_at IS NULL AND u.disabled_at IS NULL "
        "AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() "
        "FOR SHARE OF m,u,s",
        (principal.workspace_id, principal.user_id, principal.session_id),
    ).fetchone()
    if row is None or not role_permits(Role(row["role"]), Permission.WORKSPACE_CONFIGURE):
        raise Refused("current repository connection authority unavailable")


def _audit(
    conn: psycopg.Connection[Any], principal: HumanPrincipal, binding_id: str, action: str
) -> None:
    conn.execute(
        "INSERT INTO audit_event(workspace_id,actor_user,action,target_kind,target_id,"
        "outcome,occurred_at,detail) VALUES(%s,%s,%s,'github_repository_binding',%s,"
        "'ALLOWED',clock_timestamp(),'{}')",
        (principal.workspace_id, principal.user_id, action, binding_id),
    )


def connect_repository(
    database_url: str,
    *,
    principal: HumanPrincipal,
    scope: RepositoryScope,
    app_jwt: str,
    allow_temporary_token_issuance: bool = False,
    _transport: httpx.BaseTransport | None = None,
) -> str:
    """One explicitly requested connection, with no transaction held over network I/O.

    A trusted authenticated principal and operator-selected App scope are prerequisites.
    Recheck membership, account and session after remote inspection/token revocation; store
    the exact returned identity and audit atomically. No retry follows uncertain HTTP or DB
    commit. A commit error is unconfirmed, not proof that the binding never existed.
    """
    if not isinstance(principal, HumanPrincipal) or not isinstance(scope, RepositoryScope):
        raise Refused("repository connection input unavailable")
    if allow_temporary_token_issuance is not True:
        raise Refused("temporary installation-token issuance requires explicit authorization")
    with workspace_connection(database_url, principal.workspace_id) as conn:
        _authorize(conn, principal)
        existing = conn.execute(
            "SELECT id FROM github_repository_binding WHERE workspace_id=%s "
            "AND app_id=%s AND repository_id=%s AND revoked_at IS NULL",
            (principal.workspace_id, scope.app_id, scope.repository_id),
        ).fetchone()
        if existing is not None:
            raise Refused("repository already connected; replacement requires explicit disconnect")
    access = inspect_repository(
        scope,
        app_jwt=app_jwt,
        allow_temporary_token_issuance=True,
        _transport=_transport,
    )
    with workspace_connection(database_url, principal.workspace_id) as conn:
        _authorize(conn, principal)
        binding_id = github_bindings.record_verified(
            conn,
            workspace_id=principal.workspace_id,
            app_id=access.scope.app_id,
            installation_id=access.scope.installation_id,
            account_id=access.scope.account_id,
            repository_id=access.scope.repository_id,
            owner=access.scope.owner,
            name=access.scope.name,
            observed_at=access.observed_at,
        )
        _audit(conn, principal, binding_id, "GITHUB_REPOSITORY_CONNECT")
    return binding_id


def disconnect_repository(database_url: str, *, principal: HumanPrincipal, binding_id: str) -> bool:
    """Authorized, audited local disconnect. No App uninstall or remote API mutation."""
    with workspace_connection(database_url, principal.workspace_id) as conn:
        _authorize(conn, principal)
        changed = github_bindings.disconnect(
            conn, workspace_id=principal.workspace_id, binding_id=binding_id
        )
        if changed:
            _audit(conn, principal, binding_id, "GITHUB_REPOSITORY_DISCONNECT")
    return changed
