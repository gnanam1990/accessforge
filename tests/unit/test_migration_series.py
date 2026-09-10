"""The migration series is checked before any statement reaches the database.

Module 06 was cut from module 04's head and shipped migration `0007`, which references a table
`0005` creates. A fresh database failed with `relation "project" does not exist` raised from the
setup of an unrelated authentication test, and a development database carried across branches passed
because it still held `0005`'s tables and still recorded `0005` as applied. Both halves of that are
now preconditions rather than surprises.

Requirements: FR-002. Invariants: INV-01.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import accessforge_persistence
from accessforge_persistence import MIGRATIONS_DIR, MigrationSeriesError, _migration_paths


@pytest.fixture()
def series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(accessforge_persistence, "MIGRATIONS_DIR", tmp_path)
    return tmp_path


def _write(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("SELECT 1", encoding="utf-8")


def test_the_shipped_series_is_consecutive() -> None:
    """The real series, not a fabricated one. This is the assertion CI needed and did not have."""
    paths = _migration_paths()
    numbers = [int(p.name.split("_", 1)[0]) for p in paths]
    assert numbers == list(range(1, len(paths) + 1))


def test_a_gap_names_the_missing_number(series: Path) -> None:
    _write(series, "0001_a.sql", "0002_b.sql", "0004_d.sql")
    with pytest.raises(MigrationSeriesError) as caught:
        _migration_paths()
    assert "0003" in str(caught.value)
    assert "rebase" in str(caught.value), "the message should say how to recover"


def test_the_exact_shape_of_the_module_06_failure_is_refused(series: Path) -> None:
    """The concrete regression: 0001-0004 plus 0007, which is what the branch actually contained."""
    _write(
        series,
        "0001_identity_and_tenancy.sql",
        "0002_audit_split_and_own_memberships.sql",
        "0003_journal_and_outbox.sql",
        "0004_sequencer_provenance_and_tenant_keys.sql",
        "0007_journeys_and_fixtures.sql",
    )
    with pytest.raises(MigrationSeriesError, match="0005, 0006"):
        _migration_paths()


def test_two_migrations_sharing_a_number_are_refused(series: Path) -> None:
    """Two people numbering 0003 on separate branches is the ordinary way this happens."""
    _write(series, "0001_a.sql", "0002_b.sql", "0003_alice.sql", "0003_bob.sql")
    with pytest.raises(MigrationSeriesError, match="0003"):
        _migration_paths()


def test_an_unnumbered_migration_is_refused(series: Path) -> None:
    _write(series, "0001_a.sql", "cleanup.sql")
    with pytest.raises(MigrationSeriesError, match="does not begin with a number"):
        _migration_paths()


def test_a_consecutive_series_is_returned_in_order(series: Path) -> None:
    _write(series, "0002_b.sql", "0001_a.sql", "0003_c.sql")
    assert [p.name for p in _migration_paths()] == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]


def test_the_guard_reads_the_directory_it_is_told_to(series: Path) -> None:
    """Without this, every test above could be passing against the real migrations directory."""
    assert accessforge_persistence.MIGRATIONS_DIR == series
    assert series != MIGRATIONS_DIR


# --- findings from the module 06 independent review ---------------------------------------------


def test_a_migration_numbered_zero_is_refused(series: Path) -> None:
    """The gap check ran from 1 to max, so `0000_bootstrap.sql` slipped past it entirely.

    A migration outside the declared series still executes SQL, and the ledger records it as
    applied, so a tree that legitimately starts at 0001 would then be told it holds a foreign one.
    """
    _write(series, "0000_bootstrap.sql", "0001_a.sql", "0002_b.sql")
    with pytest.raises(MigrationSeriesError, match="series starts at 0001"):
        _migration_paths()


def test_a_lone_migration_numbered_zero_is_refused(series: Path) -> None:
    _write(series, "0000_bootstrap.sql")
    with pytest.raises(MigrationSeriesError, match="series starts at 0001"):
        _migration_paths()


def test_a_narrow_numeric_prefix_is_refused(series: Path) -> None:
    """`1_a.sql`, `2_b.sql`, `10_c.sql` is a consecutive series that sorts 1, 10, 2.

    Under lexical ordering `10_c.sql` would have been applied before `2_b.sql` -- a dependent
    migration ahead of its prerequisite, which is the failure the whole guard exists to prevent, and
    it would have passed every check the guard had.
    """
    _write(series, "1_a.sql", "2_b.sql", "10_c.sql")
    with pytest.raises(MigrationSeriesError, match=r"has a \d-digit number"):
        _migration_paths()


def test_a_wide_numeric_prefix_is_refused(series: Path) -> None:
    _write(series, "00001_a.sql")
    with pytest.raises(MigrationSeriesError, match="5-digit number"):
        _migration_paths()


def test_the_series_is_returned_in_numeric_order(series: Path) -> None:
    """Asserted on the parsed numbers rather than on the filenames.

    With four-digit padding the lexical and numeric orders agree, so a test comparing filenames
    would pass against either implementation and prove nothing about which one is in use.
    """
    _write(series, "0003_c.sql", "0001_a.sql", "0002_b.sql")
    assert [int(p.name.split("_", 1)[0]) for p in _migration_paths()] == [1, 2, 3]


def test_the_shipped_migrations_all_use_four_digits() -> None:
    """The real series, so the convention this guard enforces is not one only tests satisfy."""
    for path in MIGRATIONS_DIR.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        assert prefix.isdigit() and len(prefix) == 4, path.name
