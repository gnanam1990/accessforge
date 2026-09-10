"""Migrations and workspace-scoped database access.

PostgreSQL is authoritative for business state (CONTRACTS section 2). This package owns schema
migration and the connection discipline that makes row-level security actually apply; repositories
for run state belong to module 04 and will build on the same primitives.

The central idea is that a connection is *never* workspace-ambiguous. Code either holds a
workspace-scoped connection or it holds one that can see nothing, and there is no third state where
a forgotten predicate quietly returns another tenant's rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

__all__ = [
    "MIGRATIONS_DIR",
    "applied_migrations",
    "connect",
    "migrate",
    "unscoped_connection",
    "user_connection",
    "workspace_connection",
]


def connect(database_url: str) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


def _ensure_migration_table(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def applied_migrations(database_url: str) -> list[str]:
    with connect(database_url) as conn:
        _ensure_migration_table(conn)
        rows = conn.execute("SELECT name FROM schema_migration ORDER BY name").fetchall()
    return [str(r["name"]) for r in rows]


def migrate(database_url: str) -> list[str]:
    """Apply pending migrations in filename order; return what was applied.

    Each migration runs in its own transaction and is recorded in the same transaction, so a
    failure leaves neither a half-applied schema nor a false record of success.
    """
    pending: list[str] = []
    with connect(database_url) as conn:
        _ensure_migration_table(conn)
        done = {
            str(r["name"]) for r in conn.execute("SELECT name FROM schema_migration").fetchall()
        }
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in done:
            continue
        sql = path.read_text(encoding="utf-8")
        with connect(database_url) as conn, conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
        pending.append(path.name)
    return pending


@contextmanager
def workspace_connection(
    database_url: str, workspace_id: str
) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """A connection scoped to exactly one workspace for its whole lifetime.

    The workspace is established with ``SET LOCAL`` inside a transaction, so it cannot leak to the
    next user of a pooled connection — a leaked scope would be worse than none, because it would
    silently widen access rather than narrow it.

    ``set_config`` is used with a bound parameter rather than string-formatted SQL: the workspace id
    arrives from a route and must not be interpolated into a statement.
    """
    conn = connect(database_url)
    try:
        with conn, conn.transaction():
            conn.execute("SELECT set_config('accessforge.workspace_id', %s, true)", (workspace_id,))
            yield conn
    finally:
        conn.close()


@contextmanager
def user_connection(
    database_url: str, user_id: str
) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """A connection that identifies the acting user but no workspace.

    This exists for exactly one job: letting a signed-in person enumerate **their own**
    memberships, which is what a post-login workspace picker needs. Only the
    `workspace_membership` policy consults the user scope, and only to match `user_id`; every other
    workspace-scoped table stays invisible, so identifying a user is not a general-purpose bypass.

    Writes still require a workspace scope. Reading your own memberships is safe; granting one is
    not.
    """
    conn = connect(database_url)
    try:
        with conn, conn.transaction():
            conn.execute("SELECT set_config('accessforge.user_id', %s, true)", (user_id,))
            yield conn
    finally:
        conn.close()


@contextmanager
def unscoped_connection(
    database_url: str,
) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """A connection with neither a workspace nor a user established.

    Row-level security makes this see **zero** rows of workspace-scoped data, which is the correct
    default. It is for genuinely workspace-independent work — migrations, resolving a session,
    looking a user up by email, and reading or writing `global_audit_event` — and its name is
    deliberately unattractive so that reaching for it is a visible decision.

    It cannot enumerate a user's memberships; that needs `user_connection`. An earlier version of
    this docstring claimed otherwise, which was wrong: the policy excluded every row.
    """
    conn = connect(database_url)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
