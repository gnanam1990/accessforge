"""Per-run fixture instances, with the oracle kept on the trusted side.

The two properties that matter: every run gets a fresh instance, and the navigator path has no route
to the observer's expectations. Both are enforced by structure rather than by convention.

Requirements: FR-003, FR-005, FR-023. Invariants: INV-01, INV-03, INV-05, INV-07.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    fixtures,
    migrate,
    runs,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xC0))
WS_OTHER = str(uuid.UUID(int=0xC1))
MANIFEST = digest({"m": "06"})
TEMPLATE_DIGEST = digest({"template": "service-request-v1"})

NAVIGATOR_VALUES = {
    "full_name": "Test Person",
    "email_invalid": "not-an-email",
    "email_valid": "test.person@example.test",
}
OBSERVER_CONFIG = {"expected_request_count": "1"}


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _run(url: str, workspace: str = WS) -> str:
    with workspace_connection(url, workspace) as conn:
        return runs.create_run(conn, workspace_id=workspace, manifest_digest=MANIFEST)


def _instance(url: str, run_id: str, workspace: str = WS) -> fixtures.FixtureInstance:
    with workspace_connection(url, workspace) as conn:
        return fixtures.create_instance(
            conn,
            workspace_id=workspace,
            run_id=run_id,
            template_id="service-request-v1",
            template_digest=TEMPLATE_DIGEST,
            navigator_values=NAVIGATOR_VALUES,
            observer_config=OBSERVER_CONFIG,
        )


# --- freshness ---------------------------------------------------------------------------------


def test_each_run_gets_a_distinct_instance_and_nonce(db: str) -> None:
    first = _instance(db, _run(db))
    second = _instance(db, _run(db))
    assert first.instance_id != second.instance_id
    assert first.nonce != second.nonce
    assert first.template_digest == second.template_digest, "the template itself is unchanged"


def test_a_second_instance_for_the_same_run_is_refused_by_the_database(db: str) -> None:
    """Freshness enforced by a constraint, not by a convention someone has to remember."""
    run_id = _run(db)
    _instance(db, run_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _instance(db, run_id)


def test_the_instance_digest_differs_per_run_for_an_identical_template(db: str) -> None:
    """This is what stops a prior run's receipt satisfying this run's completion assertion."""
    first = _instance(db, _run(db))
    second = _instance(db, _run(db))
    assert fixtures.instance_digest(first) != fixtures.instance_digest(second)


def test_the_template_digest_is_recorded_separately_from_the_instance(db: str) -> None:
    """A template change is distinguishable from a new run of an unchanged template."""
    instance = _instance(db, _run(db))
    assert instance.template_digest == TEMPLATE_DIGEST
    assert instance.nonce not in instance.template_digest


# --- the oracle boundary -------------------------------------------------------------------------


def test_the_navigator_path_returns_only_navigator_values(db: str) -> None:
    instance = _instance(db, _run(db))
    with workspace_connection(db, WS) as conn:
        visible = fixtures.navigator_values(conn, instance_id=instance.instance_id)
    assert visible == NAVIGATOR_VALUES
    assert "expected_request_count" not in visible


def test_the_navigator_function_has_no_parameter_that_could_request_the_oracle(db: str) -> None:
    """Structural, not behavioural.

    A single function with an ``include_oracle=True`` flag is one careless call away from handing
    the
    navigator the answer key, so the two readers are separate functions with no switch between them.
    """
    import inspect

    params = set(inspect.signature(fixtures.navigator_values).parameters)
    assert params == {"conn", "instance_id"}
    assert not any("oracle" in p or "observer" in p or "all" in p for p in params)


def test_the_observer_path_is_a_separate_function(db: str) -> None:
    # Allowed-path control: trusted callers can still reach the oracle.
    instance = _instance(db, _run(db))
    with workspace_connection(db, WS) as conn:
        assert fixtures.observer_config(conn, instance_id=instance.instance_id) == OBSERVER_CONFIG


def test_instances_are_workspace_isolated(db: str) -> None:
    instance = _instance(db, _run(db))
    with workspace_connection(db, WS_OTHER) as conn:
        assert conn.execute("SELECT 1 FROM run_fixture_instance").fetchall() == []
        with pytest.raises(fixtures.FixtureError):
            fixtures.navigator_values(conn, instance_id=instance.instance_id)


def test_the_product_table_does_not_collide_with_the_application_under_test(db: str) -> None:
    """Regression: the reference application owns `fixture_instance`.

    `CREATE TABLE IF NOT EXISTS fixture_instance` did nothing, silently, and the product would have
    been reading the application's rows.

    The first version of this test asserted that both tables were present and stopped there, which
    made it depend on a database that happened to hold the application's schema too. It passed on a
    development database and proved nothing on a fresh one. It now creates the collision itself --
    the application's real schema, applied here -- and then asserts where a write actually lands.
    """
    from reference_app.db import SCHEMA as APPLICATION_SCHEMA

    try:
        with unscoped_connection(db) as conn:
            conn.execute(APPLICATION_SCHEMA)

        run_id = _run(db)
        instance = _instance(db, run_id)

        with unscoped_connection(db) as conn:
            # The application's table carries no tenant scoping, so an unscoped connection can see
            # every row it holds. It should hold none: the product never writes here.
            assert conn.execute("SELECT count(*) AS n FROM fixture_instance").fetchone() == {
                "n": 0
            }, "the product wrote into the application's table"

        with workspace_connection(db, WS) as conn:
            product = conn.execute(
                "SELECT nonce FROM run_fixture_instance WHERE id = %s", (instance.instance_id,)
            ).fetchone()
        assert product is not None, "the product's row is in the product's table"
        assert product["nonce"] == instance.nonce

        with unscoped_connection(db) as conn:
            product_columns = {
                str(r["column_name"])
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'run_fixture_instance'"
                ).fetchall()
            }
        assert "workspace_id" in product_columns, "the product's table carries tenant scoping"
    finally:
        with unscoped_connection(db) as conn:
            conn.execute("DROP TABLE IF EXISTS service_request, fixture_instance CASCADE")
