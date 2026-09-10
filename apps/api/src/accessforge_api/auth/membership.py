"""Resolving a request to an authorized principal.

The rule that matters: **workspace access is derived from current membership plus the route, never
from the request body.** A body field naming a workspace is a claim by the caller, and treating a
caller's claim as an authorization decision is the whole of the cross-tenant substitution attack.

So `resolve_human_principal` takes the workspace from the path and looks up whether this user holds
an active membership in it, right now. There is no parameter through which a body value could reach
this function.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authorization import (
    AuthorizationError,
    HumanPrincipal,
    Permission,
    Role,
)

from .sessions import AuthenticatedSession


class MembershipError(Exception):
    """The user holds no usable membership in the requested workspace."""


def resolve_human_principal(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    session: AuthenticatedSession,
    workspace_id_from_route: str,
) -> HumanPrincipal:
    """Build a principal from live membership, or raise.

    ``revoked_at IS NULL`` is part of the query rather than a check on the result: membership rows
    are retained after revocation for audit, so a query that forgot the condition would treat a
    removed member as current.
    """
    row = conn.execute(
        """
        SELECT m.role
        FROM workspace_membership m
        JOIN app_user u ON u.id = m.user_id
        WHERE m.workspace_id = %s
          AND m.user_id = %s
          AND m.revoked_at IS NULL
          AND u.disabled_at IS NULL
        """,
        (workspace_id_from_route, session.user_id),
    ).fetchone()

    if row is None:
        # Indistinguishable from "this workspace does not exist", deliberately. Telling a caller
        # that a workspace exists but is not theirs leaks the existence of other tenants.
        raise MembershipError("no active membership in this workspace")

    return HumanPrincipal(
        user_id=session.user_id,
        workspace_id=workspace_id_from_route,
        role=Role(row["role"]),
        session_id=session.session_id,
    )


def require_permission(principal: HumanPrincipal, permission: Permission) -> None:
    if not principal.permits(permission):
        raise AuthorizationError(
            f"role {principal.role} does not hold {permission} in workspace "
            f"{principal.workspace_id}"
        )


def assert_route_matches_body(
    *, workspace_id_from_route: str, workspace_id_from_body: str | None
) -> None:
    """Reject a body that disagrees with the route.

    The route value is the only one used for authorization, so a mismatched body field cannot grant
    anything. It is still refused rather than ignored: a client sending a different workspace is
    either confused or probing, and silently using the safe value would hide both.
    """
    if workspace_id_from_body is not None and workspace_id_from_body != workspace_id_from_route:
        raise AuthorizationError(
            "workspace in request body does not match the route; authorization derives from the "
            "route and membership, never from the body"
        )


def record_audit_event(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    action: str,
    target_kind: str,
    target_id: str | None,
    outcome: str,
    actor_user: str | None = None,
    actor_service: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a workspace-scoped audit row.

    ``workspace_id`` is required. It used to be optional, with NULL meaning
    "workspace-independent" — and because the policy read
    ``workspace_id IS NULL OR workspace_id = current_workspace_id()``,
    those rows were readable by **every** tenant. A sign-in row carrying an actor id and an
    originating address was visible across the whole system. Workspace-independent events now go
    to ``record_global_audit_event`` and a table a tenant connection can never read.

    Denials are recorded alongside allowances: a denial that leaves no trace is indistinguishable
    from a request nobody made.

    The table has no column for a token, password or reader transcript, so this cannot write one —
    but callers should still keep ``detail`` to identifiers and reasons.
    """
    conn.execute(
        """
        INSERT INTO audit_event
            (workspace_id, actor_user, actor_service, action, target_kind, target_id, outcome,
             occurred_at, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            workspace_id,
            actor_user,
            actor_service,
            action,
            target_kind,
            target_id,
            outcome,
            datetime.now(UTC),
            json.dumps(detail or {}),
        ),
    )


def record_global_audit_event(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    action: str,
    target_kind: str,
    target_id: str | None,
    outcome: str,
    actor_user: str | None = None,
    actor_service: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append an audit row for an event that belongs to no workspace.

    Sign-in, sign-out, account disable. Requires a connection with **no** workspace scope, because
    the table's policy is the inverse of the scoped ones: readable only when no workspace is
    established, which means an operator path rather than any tenant.
    """
    conn.execute(
        """
        INSERT INTO global_audit_event
            (actor_user, actor_service, action, target_kind, target_id, outcome, occurred_at,
             detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            actor_user,
            actor_service,
            action,
            target_kind,
            target_id,
            outcome,
            datetime.now(UTC),
            json.dumps(detail or {}),
        ),
    )
