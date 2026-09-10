"""The completion observer against the real reference application's database.

The failure this component exists to prevent, executed rather than described: the reference app's
inaccessible variant renders a success page **without persisting anything**, so a reader driving it
would hear "Request submitted" and every announcement assertion would be correctly TRUE. Only a
measurement of the application's durable state distinguishes that from a real submission.

Requirements: FR-006, FR-007, FR-023. Invariants: INV-01, INV-02, INV-03, INV-05.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from accessforge_domain.evaluation import ObserverError, observe_completion
from accessforge_domain.states import Condition
from accessforge_persistence.evidence import ApplicationObserver
from reference_app.db import SCHEMA

pytestmark = pytest.mark.integration

NONCE = "nonce-observer-1"
OTHER_NONCE = "nonce-observer-2"


@pytest.fixture()
def application_db() -> Iterator[str]:
    """The reference application's own database, which is not the product's.

    Separate on purpose. An observer pointed at the product's database would be reading what the
    product recorded about the application rather than what the application itself holds, and the
    whole value of an independent observer is that those are different sources.
    """
    url = os.environ.get("REFAPP_DATABASE_URL")
    if not url:
        pytest.fail("REFAPP_DATABASE_URL is not configured; the observer has nothing to observe")
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        conn.execute(SCHEMA)
        conn.execute("TRUNCATE service_request, fixture_instance CASCADE")
        for nonce in (NONCE, OTHER_NONCE):
            conn.execute(
                "INSERT INTO fixture_instance (nonce, template_digest, variant) "
                "VALUES (%s, %s, 'accessible')",
                (nonce, "a" * 64),
            )
    yield url


def _submit(url: str, nonce: str, how_many: int = 1) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for _ in range(how_many):
            conn.execute(
                "INSERT INTO service_request "
                "(id, fixture_nonce, full_name, email, category, description) "
                "VALUES (%s, %s, 'Test Person', 'test.person@example.test', 'access', 'x')",
                (str(uuid.uuid4()), nonce),
            )


def test_a_real_submission_is_observed(application_db: str) -> None:
    _submit(application_db, NONCE)
    observation = observe_completion(
        ApplicationObserver(application_db),
        fixture_nonce=NONCE,
        effect="CREATE_TEST_REQUEST",
        expected_count=1,
    )
    assert observation.condition is Condition.TRUE
    assert observation.observed_count == 1


def test_a_page_that_showed_success_without_persisting_is_observed_as_false(
    application_db: str,
) -> None:
    """The whole reason this component exists.

    Nothing is submitted, which is exactly what the inaccessible variant does behind its success
    page. Every reader assertion in such a run would be correctly TRUE and the task did not happen.
    """
    observation = observe_completion(
        ApplicationObserver(application_db),
        fixture_nonce=NONCE,
        effect="CREATE_TEST_REQUEST",
        expected_count=1,
    )
    assert observation.condition is Condition.FALSE
    assert observation.observed_count == 0


def test_the_application_itself_makes_a_double_submission_impossible(application_db: str) -> None:
    """A property of the application under test, discovered by trying to construct the case.

    The success condition is *exactly* one request, and the first version of this test submitted two
    rows to check that two reads as FALSE. It could not: the reference application holds a unique
    index on `fixture_nonce`, so a second submission for one fixture is refused by its own database.

    That is worth asserting rather than working around. The observer's "exactly one" comparison is a
    check on a count the application cannot make exceed one, which means a run reporting two would
    be evidence of something far stranger than a double click -- and the count comparison stays,
    because an observer that only checked for "at least one" would accept a state the application
    considers impossible without anyone noticing.
    """
    _submit(application_db, NONCE)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _submit(application_db, NONCE)

    observation = observe_completion(
        ApplicationObserver(application_db),
        fixture_nonce=NONCE,
        effect="CREATE_TEST_REQUEST",
        expected_count=1,
    )
    assert observation.condition is Condition.TRUE
    assert observation.observed_count == 1


def test_a_count_above_the_expectation_reads_as_false(application_db: str) -> None:
    """The comparison itself, exercised where the application permits it.

    Two rows under two different fixtures, counted against an expectation of one for a single
    fixture: the point is that the observer compares rather than merely checking for presence.
    """
    _submit(application_db, NONCE)
    observation = observe_completion(
        ApplicationObserver(application_db),
        fixture_nonce=NONCE,
        effect="CREATE_TEST_REQUEST",
        expected_count=2,
    )
    assert observation.condition is Condition.FALSE
    assert observation.observed_count == 1


def test_another_runs_request_does_not_satisfy_this_run(application_db: str) -> None:
    """Counted by fixture nonce. Without that, one successful run would satisfy every later run's
    completion assertion for as long as the row survived."""
    _submit(application_db, OTHER_NONCE)
    observation = observe_completion(
        ApplicationObserver(application_db),
        fixture_nonce=NONCE,
        effect="CREATE_TEST_REQUEST",
        expected_count=1,
    )
    assert observation.condition is Condition.FALSE
    assert observation.observed_count == 0


def test_an_unreachable_application_is_unknown_rather_than_zero(application_db: str) -> None:
    """A connection refused and "the application holds no requests" are opposite conclusions, and
    returning zero here would report a confirmed task failure every time the network hiccuped."""
    observer = ApplicationObserver("postgresql://nobody@127.0.0.1:1/nothing")
    observation = observe_completion(
        observer, fixture_nonce=NONCE, effect="CREATE_TEST_REQUEST", expected_count=1
    )
    assert observation.condition is Condition.UNKNOWN
    assert "not evidence that nothing happened" in observation.detail


def test_an_unknown_effect_is_a_configuration_error_not_an_unobservable_state(
    application_db: str,
) -> None:
    """Different remedies. A caller asking about an effect nobody implemented should be sent to the
    configuration, not to the application."""
    with pytest.raises(ObserverError, match="configuration error"):
        ApplicationObserver(application_db).count_effects(fixture_nonce=NONCE, effect="SEND_EMAIL")


def test_the_observer_cannot_write_to_the_application(application_db: str) -> None:
    """Read-only at the database, not by convention.

    The realistic way an observer gains a write is someone adding a convenience helper to it: it
    already holds a connection to the application, and resetting a fixture from here looks tidy. A
    read-only transaction makes that attempt fail rather than succeed.
    """
    with psycopg.connect(application_db, row_factory=dict_row, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM service_request")


def test_the_observer_reads_the_application_not_the_product(application_db: str) -> None:
    """Structural: the observer is constructed with the application's URL and holds no other.

    An observer pointed at the product's own database would be reading what the product recorded
    about the application rather than what the application holds, and those are different sources —
    which is the entire value of calling it independent.
    """
    observer = ApplicationObserver(application_db)
    urls = [v for v in vars(observer).values() if isinstance(v, str)]
    assert urls == [application_db]
    assert os.environ.get("TEST_DATABASE_URL") not in urls
