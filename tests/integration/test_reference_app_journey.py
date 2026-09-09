"""End-to-end proof against a real PostgreSQL server.

Nothing here uses an in-memory substitute. If PostgreSQL is not reachable these tests skip rather
than pass, because a green run that never touched a database would not be evidence of anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from reference_app import db
from reference_app.app import create_app
from reference_app.config import ReferenceAppSettings

pytestmark = pytest.mark.integration

OBSERVER = "observer-token-for-tests-0123456789"
SETUP = "setup-token-for-tests-0123456789"

VALID_SUBMISSION = {
    "full_name": "Test Person",
    "email": "test.person@example.com",
    "category": "access-request",
    "description": "Cannot reach the settings page using the keyboard.",
}
INVALID_SUBMISSION = {**VALID_SUBMISSION, "email": "not-an-email"}


@pytest.fixture()
def settings(test_database_url: str) -> ReferenceAppSettings:
    return ReferenceAppSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=test_database_url,
        observer_token=OBSERVER,
        setup_token=SETUP,
        environment="test",
    )


@pytest.fixture()
def client(settings: ReferenceAppSettings) -> Iterator[TestClient]:
    db.initialize(settings.database_url)
    with db.transaction(settings.database_url) as conn:
        conn.execute("TRUNCATE service_request, fixture_instance CASCADE")
    with TestClient(create_app(settings)) as c:
        yield c


def _new_fixture(client: TestClient, variant: str = "inaccessible") -> str:
    r = client.post(
        "/api/_test/fixtures", params={"variant": variant}, headers={"x-setup-token": SETUP}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["nonce"])


# --- the journey ------------------------------------------------------------------------


def test_full_journey_creates_exactly_one_request(client: TestClient) -> None:
    nonce = _new_fixture(client)

    assert client.get(f"/form/{nonce}").status_code == 200

    rejected = client.post(f"/form/{nonce}", data=INVALID_SUBMISSION)
    assert rejected.status_code == 422
    assert "Enter an email address" in rejected.text

    # The rejected attempt must not have created anything. A form that showed an error while
    # quietly persisting a row would make the whole journey unfalsifiable.
    receipt = client.get(
        f"/api/_test/receipt/{nonce}", headers={"x-observer-token": OBSERVER}
    ).json()
    assert receipt["request_count"] == 0

    accepted = client.post(f"/form/{nonce}", data=VALID_SUBMISSION)
    assert accepted.status_code == 201
    assert "Request received" in accepted.text

    receipt = client.get(
        f"/api/_test/receipt/{nonce}", headers={"x-observer-token": OBSERVER}
    ).json()
    assert receipt["request_count"] == 1
    assert receipt["requests"][0]["email"] == VALID_SUBMISSION["email"]


def test_resubmission_is_a_visible_conflict(client: TestClient) -> None:
    nonce = _new_fixture(client)
    assert client.post(f"/form/{nonce}", data=VALID_SUBMISSION).status_code == 201
    assert client.post(f"/form/{nonce}", data=VALID_SUBMISSION).status_code == 409
    receipt = client.get(
        f"/api/_test/receipt/{nonce}", headers={"x-observer-token": OBSERVER}
    ).json()
    assert receipt["request_count"] == 1


def test_a_rendered_success_page_is_not_a_receipt(client: TestClient) -> None:
    """A frontend-only form cannot satisfy the receipt test.

    The observer reads durable state, not the page. Submitting against a fixture nonce that was
    never created cannot produce a receipt no matter what any page displays.
    """
    r = client.post("/form/never-created-nonce", data=VALID_SUBMISSION)
    assert r.status_code == 404
    receipt = client.get(
        "/api/_test/receipt/never-created-nonce", headers={"x-observer-token": OBSERVER}
    ).json()
    assert receipt["request_count"] == 0


# --- identity boundaries ----------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"x-observer-token": "wrong"}, {"x-observer-token": ""}])
def test_receipt_requires_the_observer_identity(
    client: TestClient, headers: dict[str, str]
) -> None:
    nonce = _new_fixture(client)
    assert client.get(f"/api/_test/receipt/{nonce}", headers=headers).status_code == 403


def test_navigator_cannot_reach_the_observer_receipt_with_the_setup_token(
    client: TestClient,
) -> None:
    # The two test identities are distinct on purpose; one must not stand in for the other.
    nonce = _new_fixture(client)
    r = client.get(f"/api/_test/receipt/{nonce}", headers={"x-observer-token": SETUP})
    assert r.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"x-setup-token": "wrong"}])
def test_reset_requires_the_setup_identity(client: TestClient, headers: dict[str, str]) -> None:
    assert client.post("/api/_test/reset", headers=headers).status_code == 403


def test_unauthorized_reset_does_not_destroy_data(client: TestClient) -> None:
    nonce = _new_fixture(client)
    client.post(f"/form/{nonce}", data=VALID_SUBMISSION)
    assert client.post("/api/_test/reset", headers={"x-setup-token": "wrong"}).status_code == 403
    receipt = client.get(
        f"/api/_test/receipt/{nonce}", headers={"x-observer-token": OBSERVER}
    ).json()
    assert receipt["request_count"] == 1


# --- durability -------------------------------------------------------------------------


def test_submission_survives_application_restart(settings: ReferenceAppSettings) -> None:
    db.initialize(settings.database_url)
    with db.transaction(settings.database_url) as conn:
        conn.execute("TRUNCATE service_request, fixture_instance CASCADE")

    with TestClient(create_app(settings)) as first:
        nonce = _new_fixture(first)
        assert first.post(f"/form/{nonce}", data=VALID_SUBMISSION).status_code == 201

    # A completely new application instance, new connections, no shared process state.
    with TestClient(create_app(settings)) as second:
        receipt = second.get(
            f"/api/_test/receipt/{nonce}", headers={"x-observer-token": OBSERVER}
        ).json()
        assert receipt["request_count"] == 1


# --- health -----------------------------------------------------------------------------


def test_readiness_passes_when_the_database_is_reachable(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_readiness_fails_when_the_database_is_unreachable(
    settings: ReferenceAppSettings,
) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgresql://nobody:nobody@localhost:5432/does_not_exist_db"}
    )
    with TestClient(create_app(broken)) as c:
        # Liveness is about the process; it must still answer.
        assert c.get("/health/live").status_code == 200
        # Readiness is about dependencies; a missing database must fail visibly.
        r = c.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "not-ready"


def test_dependency_failure_does_not_leak_credentials(settings: ReferenceAppSettings) -> None:
    secret = "sup3rs3cr3t-should-never-appear"
    broken = settings.model_copy(
        update={"database_url": f"postgresql://user:{secret}@localhost:5432/does_not_exist_db"}
    )
    with TestClient(create_app(broken)) as c:
        body = c.get("/health/ready").text + c.get("/diagnostics").text
    assert secret not in body
    assert "<redacted>" in body


def test_database_really_is_postgres(test_database_url: str) -> None:
    # Guards against a future refactor quietly swapping in something that merely speaks SQL.
    with psycopg.connect(test_database_url) as conn:
        version = conn.execute("SELECT version()").fetchone()
    assert version is not None and "PostgreSQL" in version[0]


def test_integration_suite_is_actually_configured() -> None:
    # A skipped integration suite must not be mistaken for a passing one.
    assert os.environ.get("TEST_DATABASE_URL"), "integration proof requires TEST_DATABASE_URL"
