"""Readiness, schema compatibility and what the control plane refuses to claim.

An orchestrator routes traffic on the strength of `/health/ready`. Three properties make that safe,
and all three are easy to lose in a refactor that "simplifies" the endpoint:

**A reachable database is not a servable one.** A process whose schema is behind its code answers
requests against missing columns, and a process whose schema is *ahead* reads unknown columns as
absent -- which is how a rollback discards data instead of failing. Both must fail readiness.

**Readiness is about this process.** It cannot establish that a physical desktop somewhere is
attached and driving a real screen reader, so it says so explicitly rather than staying silent and
letting the reader assume.

**Startup does not migrate.** Two replicas booting together would migrate concurrently, and a binary
that migrated on boot would turn a rollback into a second upgrade.

Requirements: FR-014, FR-020. Invariants: INV-13.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.config import ApiSettings
from accessforge_persistence import connect, migrate

pytestmark = pytest.mark.integration


def _settings(database_url: str) -> ApiSettings:
    return ApiSettings(
        database_url=database_url,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture()
def migrated(test_database_url: str) -> str:
    migrate(test_database_url)
    return test_database_url


@pytest.fixture()
def unmigrated(backup_database_url: str) -> Iterator[str]:
    """An empty database with no `schema_migration` table at all.

    The state a deploy is in between "the database exists" and "the migrator has run", which is
    exactly the window in which a process must not start serving.
    """
    name = f"accessforge_ready_{uuid.uuid4().hex[:8]}"
    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - generated name, not user input
    try:
        yield _with_database(backup_database_url, name)
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608


def test_readiness_reports_the_schema_it_is_serving(migrated: str) -> None:
    with TestClient(create_app(_settings(migrated))) as client:
        body = client.get("/health/ready").json()
    assert body["status"] == "ready"
    assert body["dependencies"]["schema"]["ok"] is True
    assert body["dependencies"]["schema"]["detail"].endswith(".sql")


def test_an_unmigrated_database_is_not_ready(unmigrated: str) -> None:
    with TestClient(create_app(_settings(unmigrated))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not-ready"
    assert body["dependencies"]["schema"]["ok"] is False
    assert "behind" in body["dependencies"]["schema"]["detail"]


def test_starting_against_an_unmigrated_database_does_not_migrate_it(unmigrated: str) -> None:
    """The property that makes a rolling deploy and a rollback safe.

    If startup migrated, this database would come out of the `with` block fully migrated -- and a
    rollback to an older binary would migrate forward again on boot, which is a second upgrade
    wearing a rollback's name.
    """
    with TestClient(create_app(_settings(unmigrated))) as client:
        assert client.get("/health/live").status_code == 200

    with connect(unmigrated) as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'schema_migration'"
        ).fetchone()
    assert row is not None and int(row["n"]) == 0


def test_a_database_ahead_of_this_build_is_not_ready(unmigrated: str) -> None:
    """A rollback must fail loudly rather than read newer columns as absent."""
    migrate(unmigrated)
    with connect(unmigrated) as conn:
        conn.execute("INSERT INTO schema_migration (name) VALUES ('0099_from_the_future.sql')")
        conn.commit()

    with TestClient(create_app(_settings(unmigrated))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert "ahead" in response.json()["dependencies"]["schema"]["detail"]


def test_liveness_still_answers_when_the_schema_is_unservable(unmigrated: str) -> None:
    """Liveness is about the process. Conflating the two produces a crash loop, and a crash loop
    reports "the container is broken" rather than "the migrator has not run"."""
    with TestClient(create_app(_settings(unmigrated))) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_readiness_does_not_claim_a_physical_runner_is_ready(migrated: str) -> None:
    """The product's own headline failure, committed in its health endpoint, is what this refuses.

    The note sits outside `dependencies` on purpose: everything in there contributes to the verdict,
    and folding the runner in either way would be a lie -- as a passing check it claims a desktop
    exists, and as a failing one it makes a healthy control plane look broken.
    """
    with TestClient(create_app(_settings(migrated))) as client:
        body = client.get("/health/ready").json()

    assert "desktopRunner" not in body["dependencies"]
    note = body["desktopRunner"]
    assert note["observed"] == "not-checked"
    assert "preflight" in note["detail"]
    assert body["status"] == "ready"


def test_readiness_never_echoes_a_credential(unmigrated: str) -> None:
    secret = "sup3rs3cr3t-should-never-appear"
    broken = _settings(unmigrated).model_copy(
        update={"database_url": f"postgresql://user:{secret}@127.0.0.1:5432/nope"}
    )
    with TestClient(create_app(broken)) as client:
        body = client.get("/health/ready").text
    assert secret not in body
