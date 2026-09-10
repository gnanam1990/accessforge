"""Shared route plumbing.

`workspace_scope` is the piece worth reading. Every route depends on it, and it returns a connection
already scoped to the workspace in the path — so a query that forgets its tenant predicate returns
nothing rather than another tenant's rows. Row-level security does the work; this makes sure the
connection it acts on is the right one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
from fastapi import Request

from accessforge_api.dependencies import RequestContext, build_context
from accessforge_api.problems import ProblemDetail, SafeFieldPolicy
from accessforge_domain.authorization.roles import Permission
from accessforge_persistence import workspace_connection


def database_url(request: Request) -> str:
    return str(request.app.state.config.database_url)


def workspace_scope(request: Request, workspace_id: str) -> Iterator[psycopg.Connection[Any]]:
    with workspace_connection(database_url(request), workspace_id) as conn:
        yield conn


def authorize(
    conn: psycopg.Connection[Any],
    request: Request,
    workspace_id: str,
    permission: Permission,
    body: dict[str, Any] | None = None,
    allowed_fields: frozenset[str] | None = None,
) -> RequestContext:
    """Resolve authority and validate the body's shape in one step.

    Taking `allowed_fields` here rather than leaving it to each route means a route that forgets it
    has no body validation at all — visible, rather than a route that quietly accepts anything.
    """
    if body is not None and allowed_fields is not None:
        SafeFieldPolicy(allowed_fields).assert_no_unexpected(body)
    return build_context(conn, request, workspace_id=workspace_id, permission=permission, body=body)


def as_identifier(value: str, *, what: str) -> str:
    """Refuse a value that is not a UUID, before it reaches a UUID comparison.

    PostgreSQL raises `invalid input syntax for type uuid` on a malformed value, and that surfaces
    as an unhandled driver error and a 500 with a stack trace in the log. Every identifier these
    routes receive arrives from a path segment or a query string, so every one of them is supplied
    by whoever made the request.

    A 400 rather than a 404: the value is not a well-formed reference to anything, which is a
    different statement from "no such resource is available to you" — and it discloses nothing,
    because it is decided without looking anything up.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        from accessforge_api.problems import ProblemCode

        raise ProblemDetail(ProblemCode.INVALID_INPUT, f"{what} is not a valid identifier") from exc
    return value


def as_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        from accessforge_api.problems import ProblemCode

        raise ProblemDetail(ProblemCode.INVALID_INPUT, "the request body must be a JSON object")
    return payload
