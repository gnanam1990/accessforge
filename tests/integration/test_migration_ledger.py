"""The ledger is compared against the tree, not only consulted.

`migrate` skipped anything already recorded and never asked the converse question: does this
database record a migration this tree does not contain? A development database carried from one
branch to another answered yes, and every subsequent `CREATE TABLE IF NOT EXISTS` made the mismatch
look like success.

Requirements: FR-002. Invariants: INV-01.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from accessforge_persistence import (
    MigrationSeriesError,
    applied_migrations,
    assert_row_level_security_enforced,
    connect,
    migrate,
)

pytestmark = pytest.mark.integration

FOREIGN = "9999_applied_by_another_branch.sql"
LAST = "0007_journeys_and_fixtures.sql"


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    yield test_database_url
    with connect(test_database_url) as conn, conn.transaction():
        conn.execute("DELETE FROM schema_migration WHERE name = %s", (FOREIGN,))


def test_a_migration_recorded_but_absent_from_the_tree_is_refused(db: str) -> None:
    with connect(db) as conn, conn.transaction():
        conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (FOREIGN,))

    with pytest.raises(MigrationSeriesError) as caught:
        migrate(db)
    message = str(caught.value)
    assert FOREIGN in message, "the message should name the migration that does not exist here"
    assert "fresh database" in message, "the message should say how to recover"


def test_the_refusal_happens_before_any_migration_is_applied(db: str) -> None:
    """Ordering matters: a half-applied schema on a foreign one is worse than a clear stop."""
    with connect(db) as conn, conn.transaction():
        conn.execute("DELETE FROM schema_migration WHERE name = %s", (LAST,))
        conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (FOREIGN,))

    before = set(applied_migrations(db))
    with pytest.raises(MigrationSeriesError):
        migrate(db)
    assert set(applied_migrations(db)) == before, "nothing was applied"

    with connect(db) as conn, conn.transaction():
        conn.execute("DELETE FROM schema_migration WHERE name = %s", (FOREIGN,))
        conn.execute(
            "INSERT INTO schema_migration (name) VALUES (%s) ON CONFLICT DO NOTHING",
            (LAST,),
        )


def test_a_matching_tree_migrates_to_completion(db: str) -> None:
    """The allowed path, so the guard above is not simply refusing everything."""
    assert migrate(db) == [], "an up-to-date database has nothing pending"
    recorded = applied_migrations(db)
    assert recorded == sorted(recorded)
    assert "0001_identity_and_tenancy.sql" in recorded
    assert LAST in recorded
