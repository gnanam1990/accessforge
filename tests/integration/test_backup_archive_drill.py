"""The encrypted backup archive, exercised through the operator's own scripts.

`test_restore_drill.py` proves what reconciliation does to a restored database. This file proves the
layer above it: that the commands in the runbook actually produce a file, that the file is encrypted
and self-describing, that a restore from it lands real data, and that the guards which stop an
operator destroying the thing they are recovering actually fire.

The scripts are executed as scripts -- `uv run python scripts/backup.py` -- rather than imported and
called. What is being tested is the command an operator types at three in the morning, and a test
that imported `main()` would pass while the argument parsing, the exit codes and the shebang path
were all wrong.

Requirements: FR-015, FR-020, FR-022.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from accessforge_evidence.envelope import read_header
from accessforge_persistence import connect, expected_migrations, migrate

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
WS = str(uuid.UUID(int=0x2A0))


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _script(
    name: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one of the operator scripts exactly as the runbook tells an operator to."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / name), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture()
def source(test_database_url: str, backup_database_url: str) -> Iterator[str]:
    """A small database with one recognisable row, backed up through the elevated role."""
    migrate(test_database_url)
    with connect(backup_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Archive drill')", (WS,))
        conn.commit()
    yield backup_database_url


@pytest.fixture()
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    result = _script(
        "backup.py",
        "--key-file",
        str(path),
        "--write-new-key",
        env={"ACCESSFORGE_BACKUP_DATABASE_URL": "", "ACCESSFORGE_DATABASE_URL": ""},
    )
    # Creating a key is a standalone operation: it is what an operator does before they have
    # anything to back up. An earlier version passed a throwaway --output and a comment claiming
    # the key was written first; CI proved otherwise, because the database check ran before the
    # key was written and the fixture got exit 2 and no file.
    assert result.returncode == 0, result.stderr
    assert path.exists(), result.stderr
    return path


@pytest.fixture()
def archive(source: str, key_file: Path, tmp_path: Path) -> Path:
    out = tmp_path / "backup.afbk"
    result = _script(
        "backup.py",
        "--output",
        str(out),
        "--key-file",
        str(key_file),
        "--key-id",
        "af-backup-test",
        "--skip-evidence",
        env={"ACCESSFORGE_BACKUP_DATABASE_URL": source},
    )
    assert result.returncode == 0, result.stderr
    return out


@pytest.fixture()
def empty_target(backup_database_url: str) -> Iterator[str]:
    name = f"accessforge_archive_{uuid.uuid4().hex[:8]}"
    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - generated name, not user input
    try:
        yield _with_database(backup_database_url, name)
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608


def test_the_archive_is_not_readable_without_the_key(archive: Path) -> None:
    """The property the whole envelope exists for, asserted on the bytes on disk.

    A backup is the densest concentration of other people's data this system produces and the file
    most likely to be copied somewhere nobody is watching. Searching the ciphertext for a string
    that is certainly in the plaintext is a crude check and it is the right one: it fails
    immediately if somebody ever "temporarily" writes the tar out unencrypted.
    """
    blob = archive.read_bytes()
    assert b"Archive drill" not in blob
    assert b"CREATE TABLE" not in blob
    assert b"workspace" not in blob


def test_the_archive_says_which_key_it_needs_without_revealing_it(
    archive: Path, key_file: Path
) -> None:
    with archive.open("rb") as handle:
        header = read_header(handle)
    assert header.key_id == "af-backup-test"
    key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
    assert key not in archive.read_bytes()[:4096]


def test_inspect_reads_the_manifest_without_touching_a_database(
    archive: Path, key_file: Path
) -> None:
    """The question actually asked during an incident: what is in this file?

    `--inspect` requires a `--target-database-url` because the parser does, and deliberately never
    connects to it. The target below does not exist; if `--inspect` opened it, this fails.
    """
    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        "postgresql://nobody@127.0.0.1:1/definitely_not_a_database",
        "--inspect",
    )
    assert result.returncode == 0, result.stderr
    # The newest migration by name, read from the tree rather than written here: hard-coding it
    # made this test fail the day the next migration landed, for no reason connected to --inspect.
    assert expected_migrations()[-1] in result.stdout
    assert "omits:" in result.stdout
    assert "Private signing key material" in result.stdout


def test_the_manifest_states_what_the_backup_does_not_contain(
    archive: Path, key_file: Path
) -> None:
    """Omissions are recorded in the file, not left to be discovered during an outage."""
    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        "postgresql://nobody@127.0.0.1:1/none",
        "--inspect",
    )
    assert "retention deletion" in result.stdout
    assert "physical desktop runner" in result.stdout


def test_a_restore_lands_the_data_and_reconciles_it(
    archive: Path, key_file: Path, empty_target: str
) -> None:
    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        empty_target,
        "--operator",
        "archive-drill",
    )
    assert result.returncode == 0, result.stderr
    assert "reconciled:" in result.stdout

    with connect(empty_target) as conn:
        row = conn.execute("SELECT name FROM workspace WHERE id = %s", (WS,)).fetchone()
        assert row is not None and row["name"] == "Archive drill"
        audit = conn.execute(
            "SELECT count(*) AS n FROM global_audit_event WHERE action = 'restore-reconciliation'"
        ).fetchone()
        assert audit is not None and int(audit["n"]) == 1


def test_restoring_over_the_source_database_is_refused(
    archive: Path, key_file: Path, source: str
) -> None:
    """The mistake that turns a recovery into the loss.

    Compared by host, port and database name rather than by string, so the loopback spelling below
    -- a different string naming the same database -- is still caught.
    """
    parts = urlsplit(source)
    by_ip = urlunsplit(
        (parts.scheme, f"{parts.username}@127.0.0.1:{parts.port or 5432}", parts.path, "", "")
    )
    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        by_ip,
    )
    assert result.returncode == 2
    assert "not a recovery" in result.stderr


def test_restoring_into_a_populated_database_is_refused_before_anything_is_written(
    archive: Path, key_file: Path, empty_target: str
) -> None:
    """A half-restore leaves neither the old contents nor the new."""
    first = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        empty_target,
    )
    assert first.returncode == 0, first.stderr

    second = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        empty_target,
    )
    assert second.returncode == 2
    assert "already contains" in second.stderr


def test_restoring_without_reconciling_says_what_is_now_live(
    archive: Path, key_file: Path, empty_target: str
) -> None:
    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(key_file),
        "--target-database-url",
        empty_target,
        "--no-reconcile",
    )
    assert result.returncode == 0, result.stderr
    assert "NOT RECONCILED" in result.stdout
    assert "Do not point a running deployment at it" in result.stdout

    with connect(empty_target) as conn:
        audit = conn.execute(
            "SELECT count(*) AS n FROM global_audit_event WHERE action = 'restore-reconciliation'"
        ).fetchone()
        assert audit is not None and int(audit["n"]) == 0


def test_a_truncated_archive_is_refused_rather_than_partially_restored(
    archive: Path, key_file: Path, empty_target: str, tmp_path: Path
) -> None:
    """The silent failure the framing exists to prevent, driven through the real command."""
    cut = tmp_path / "truncated.afbk"
    cut.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])
    result = _script(
        "restore.py",
        "--archive",
        str(cut),
        "--key-file",
        str(key_file),
        "--target-database-url",
        empty_target,
    )
    assert result.returncode == 1
    assert "truncat" in result.stderr.lower()

    with connect(empty_target) as conn:
        tables = conn.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()
        assert tables is not None and int(tables["n"]) == 0


def test_the_wrong_key_restores_nothing(archive: Path, empty_target: str, tmp_path: Path) -> None:
    other = tmp_path / "other.key"
    created = _script(
        "backup.py",
        "--key-file",
        str(other),
        "--write-new-key",
        env={"ACCESSFORGE_BACKUP_DATABASE_URL": "", "ACCESSFORGE_DATABASE_URL": ""},
    )
    assert created.returncode == 0, created.stderr
    assert other.exists()

    result = _script(
        "restore.py",
        "--archive",
        str(archive),
        "--key-file",
        str(other),
        "--target-database-url",
        empty_target,
    )
    assert result.returncode == 1
    assert "does not authenticate" in result.stderr


def test_the_backup_carries_no_credential_values(
    archive: Path, key_file: Path, tmp_path: Path
) -> None:
    """Configuration travels as names and shapes. Values do not.

    A backup holding the object-store credential would put the ciphertext and the key to the store
    it was copied to in one file, and the first person to leak a backup would leak the live system
    with it.
    """
    import io
    import tarfile

    from accessforge_evidence.envelope import open_sealed

    key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
    plain = io.BytesIO()
    with archive.open("rb") as handle:
        open_sealed(handle, plain, key=key)
    plain.seek(0)
    with tarfile.open(fileobj=plain, mode="r") as tar:
        member = tar.extractfile("configuration.json")
        assert member is not None
        configuration = json.loads(member.read())

    assert configuration["valuesIncluded"] is False
    assert "ACCESSFORGE_EVIDENCE_SECRET_KEY" in configuration["requiredVariables"]
    secret = os.environ.get("ACCESSFORGE_EVIDENCE_SECRET_KEY", "")
    if secret:
        assert secret not in json.dumps(configuration)


def test_the_backup_carries_key_metadata_but_no_private_key(archive: Path, key_file: Path) -> None:
    import io
    import tarfile

    from accessforge_evidence.envelope import open_sealed

    key = base64.b64decode(key_file.read_text(encoding="utf-8").strip())
    plain = io.BytesIO()
    with archive.open("rb") as handle:
        open_sealed(handle, plain, key=key)
    plain.seek(0)
    with tarfile.open(fileobj=plain, mode="r") as tar:
        member = tar.extractfile("keys.json")
        assert member is not None
        keys = json.loads(member.read())

    assert keys["privateKeyMaterialIncluded"] is False
    assert "verify" in keys["detail"]


def test_creating_a_key_needs_no_database_and_refuses_to_overwrite_one(tmp_path: Path) -> None:
    """Two properties of the operation an operator performs first, and gets one chance at.

    No database, because generating a key is not taking a backup. No overwrite, because the key
    that gets overwritten is the one every existing archive was sealed with, and there is no
    recovery from that -- so it is a refusal rather than a prompt.
    """
    path = tmp_path / "first.key"
    first = _script(
        "backup.py",
        "--key-file",
        str(path),
        "--write-new-key",
        env={"ACCESSFORGE_BACKUP_DATABASE_URL": "", "ACCESSFORGE_DATABASE_URL": ""},
    )
    assert first.returncode == 0, first.stderr
    original = path.read_bytes()

    second = _script(
        "backup.py",
        "--key-file",
        str(path),
        "--write-new-key",
        env={"ACCESSFORGE_BACKUP_DATABASE_URL": "", "ACCESSFORGE_DATABASE_URL": ""},
    )
    assert second.returncode == 2
    assert "permanently unreadable" in second.stderr
    assert path.read_bytes() == original
