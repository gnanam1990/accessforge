"""Migrations, workspace-scoped database access, and the durable journal.

Submodules: ``runs`` (reducer-backed run persistence), ``idempotency``, ``outbox`` (transactional
outbox and durable jobs), ``sequencer`` (the single trusted evidence sequencer), ``recovery``,
``transport`` and ``metrics``.

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
    "MigrationSeriesError",
    "RowLevelSecurityNotEnforced",
    "applied_migrations",
    "assert_row_level_security_enforced",
    "connect",
    "migrate",
    "unscoped_connection",
    "user_connection",
    "workspace_connection",
]


class MigrationSeriesError(RuntimeError):
    """The migrations in this tree are not a coherent series, or do not match this database."""


def _migration_paths() -> list[Path]:
    """The migration series, checked for gaps before a single statement is sent.

    Every migration is numbered, and the numbers have to be consecutive from 0001. A branch that
    carries `0007` without the `0005` it depends on is not a migration series, it is a broken one,
    and the failure it produces otherwise is `relation "project" does not exist` raised from deep
    inside an unrelated fixture. This turns that into one sentence naming the missing number.
    """
    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not paths:  # pragma: no cover - the package always ships migrations
        raise MigrationSeriesError(f"no migrations found in {MIGRATIONS_DIR}")

    numbers: dict[int, str] = {}
    for path in paths:
        prefix = path.name.split("_", 1)[0]
        if not prefix.isdigit():
            raise MigrationSeriesError(
                f"migration {path.name!r} does not begin with a number, so its place in the "
                "series is undefined"
            )
        number = int(prefix)
        if number in numbers:
            raise MigrationSeriesError(
                f"migrations {numbers[number]!r} and {path.name!r} share the number {number:04d}; "
                "their relative order would depend on the rest of the filename"
            )
        numbers[number] = path.name

    expected = range(1, max(numbers) + 1)
    missing = sorted(set(expected) - set(numbers))
    if missing:
        gap = ", ".join(f"{n:04d}" for n in missing)
        raise MigrationSeriesError(
            f"the migration series has gaps at {gap}. A later migration almost certainly depends "
            "on what the missing one creates. If this branch was cut before those migrations "
            "landed, rebase it onto a base that contains them."
        )
    return paths


class RowLevelSecurityNotEnforced(RuntimeError):
    """The connected role bypasses row-level security, so isolation is not in effect."""


def assert_row_level_security_enforced(database_url: str) -> None:
    """Fail loudly if the connected role can bypass row-level security.

    A PostgreSQL superuser — and any role with BYPASSRLS — ignores every policy, including `FORCE`.
    The official postgres container creates `POSTGRES_USER` as a superuser, so a CI job that simply
    uses it runs the entire isolation suite against no isolation at all.

    CI discovered this the hard way: sixteen isolation tests failed at once, which is the right
    outcome but a terrible diagnosis. This check turns that into one sentence naming the cause.

    It also guards the more dangerous direction. These tests fail when RLS is absent because they
    issue raw SQL, but a future test asserting isolation through application code would *pass*
    against a bypassing role — green, and proving nothing.
    """
    with connect(database_url) as conn:
        row = conn.execute(
            "SELECT current_user AS role, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone()

    if row is None:  # pragma: no cover - a connected role always has a pg_roles entry
        raise RowLevelSecurityNotEnforced("cannot determine the privileges of the connected role")

    if row["rolsuper"] or row["rolbypassrls"]:
        reason = "a superuser" if row["rolsuper"] else "granted BYPASSRLS"
        raise RowLevelSecurityNotEnforced(
            f"the connected role {row['role']!r} is {reason}, so row-level security is not "
            "enforced and every tenant-isolation assertion in this suite is meaningless. "
            "Point the connection at a role created with NOSUPERUSER NOBYPASSRLS. Note that the "
            "official postgres image creates POSTGRES_USER as a superuser."
        )


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

    Two preconditions are checked before anything is applied: the series on disk has no gaps, and
    the database records nothing this tree does not contain. Either condition, left unchecked,
    produces a confident green run against a schema nobody described.
    """
    paths = _migration_paths()
    pending: list[str] = []
    with connect(database_url) as conn:
        _ensure_migration_table(conn)
        done = {
            str(r["name"]) for r in conn.execute("SELECT name FROM schema_migration").fetchall()
        }

    # A name recorded here but absent from the tree means this database was migrated by a different
    # tree. Continuing would apply the remaining migrations on top of a schema this code has never
    # described, and `CREATE TABLE IF NOT EXISTS` would make the result look like success. A
    # development database carried across branches hid exactly this for one module.
    stale = sorted(done - {p.name for p in paths})
    if stale:
        raise MigrationSeriesError(
            f"this database records migrations that are not in this tree: {', '.join(stale)}. Its "
            "schema was built by a different branch, so nothing applied on top of it can be "
            "trusted. Use a fresh database, or check out a tree that contains them."
        )

    for path in paths:
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
