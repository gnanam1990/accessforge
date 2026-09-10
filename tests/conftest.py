from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    for name in (".env", ".env.refapp", ".env.test", ".env.objectstore"):
        _load_file(ROOT / name)


def _load_file(env: Path) -> None:
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; integration proof unavailable")
    return url


@pytest.fixture(scope="session")
def backup_database_url() -> str:
    """A connection that can actually take a backup.

    `FORCE ROW LEVEL SECURITY` applies to the table owner, so `pg_dump` run as the application role
    **fails** — it cannot read the rows it owns. A backup therefore requires a role with
    `BYPASSRLS` or a superuser, and that is an operational fact rather than a test detail: a backup
    script running as the application role produces no backup, and finds out when it is restored.

    Fails rather than skips. A restore drill that quietly did not run is worse than no drill,
    because the handoff would still say the suite was green.
    """
    url = os.environ.get("BACKUP_DATABASE_URL") or os.environ.get("SUPERUSER_URL")
    if not url:
        pytest.fail(
            "neither BACKUP_DATABASE_URL nor SUPERUSER_URL is set, so no role here can read the "
            "rows a backup needs. See .env.test.example: FORCE ROW LEVEL SECURITY means the "
            "application role cannot dump its own tables."
        )
    return url
