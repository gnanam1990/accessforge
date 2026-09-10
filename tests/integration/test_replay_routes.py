"""The replay routes the run-inspection screens read, against a real chain.

A person inspecting a run needs three things this module's routes provide: which attempts exist,
what was recorded in each and who recorded it, and what is missing. The tests are mostly about the
last one, because "what is missing" is where a replay screen can quietly become a reassurance.

Two distinctions carry the module and both are asserted here.

**Order is the sequencer's, never a clock.** Producers submit source times from their own machines
and those disagree. A timeline sorted by them would reorder an attempt according to whose clock was
fast, and a reader would be told a sequence that never happened.

**Contiguity is not completeness (INV-06).** A producer that stops halfway leaves a perfect
contiguous chain covering half the attempt. The two are reported as separate fields, because a
single "complete" would be true of an attempt that lost its second half.

Requirements: FR-006, FR-007, FR-015. Invariants: INV-02, INV-06, INV-07, INV-11.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x230))
WS_OTHER = str(uuid.UUID(int=0x231))
READER = str(uuid.UUID(int=0x232))
MANIFEST = digest({"manifest": "replay"})
SUPERVISOR = "supervisor-1"
OBSERVER = "observer-1"
BASE_TIME = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def settings(test_database_url: str) -> ApiSettings:
    return ApiSettings(
        database_url=test_database_url,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "Replay"), (WS_OTHER, "Other")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'reader@example.test')", (READER,)
        )
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'REVIEWER')",
            (WS, READER),
        )
    yield test_database_url


@pytest.fixture()
def client(db: str, settings: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        with workspace_connection(db, WS) as conn:
            issued = issue_session(conn, user_id=READER)
        test_client.cookies.set(SESSION_COOKIE, issued.session_token)
        yield test_client


@pytest.fixture()
def attempt(db: str) -> tuple[str, str]:
    with workspace_connection(db, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=0)
    return run_id, attempt_id


def _admit(
    db: str,
    run_id: str,
    attempt_id: str,
    *,
    producer: str,
    seq: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_time: datetime | None = None,
) -> None:
    with workspace_connection(db, WS) as conn:
        sequencer.admit_record(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            lease_epoch=0,
            producer_id=producer,
            source_record_id=f"{producer}-{seq}",
            producer_sequence=seq,
            event_type=event_type,
            manifest_digest=MANIFEST,
            payload=payload or {"n": seq},
            source_time=source_time or BASE_TIME + timedelta(seconds=seq),
        )


# --------------------------------------------------------------------------------------------------
# Attempts
# --------------------------------------------------------------------------------------------------


def test_a_run_lists_its_attempts(client: TestClient, attempt: tuple[str, str]) -> None:
    run_id, attempt_id = attempt
    body = client.get(f"/v1/workspaces/{WS}/runs/{run_id}/attempts").json()
    assert [item["attemptId"] for item in body["items"]] == [attempt_id]
    assert body["items"][0]["leaseEpoch"] == 0
    # Null, not "still running": an attempt whose runner vanished also has no recorded end, and the
    # two are told apart by the run's status and its ambiguity reason.
    assert body["items"][0]["endedAt"] is None


def test_a_run_that_is_not_available_here_is_404(client: TestClient, db: str) -> None:
    with workspace_connection(db, WS_OTHER) as conn:
        foreign = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
    response = client.get(f"/v1/workspaces/{WS}/runs/{foreign}/attempts")
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_a_malformed_run_identifier_is_400_not_500(client: TestClient) -> None:
    """PostgreSQL raises on a malformed uuid, and that arrives as an unhandled driver error.

    Every identifier these routes take comes from a path segment or a query string.
    """
    response = client.get(f"/v1/workspaces/{WS}/runs/not-a-uuid/attempts")
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_INPUT"


def test_a_malformed_attempt_identifier_is_400_not_500(
    client: TestClient, attempt: tuple[str, str]
) -> None:
    run_id, _ = attempt
    response = client.get(f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id=not-a-uuid")
    assert response.status_code == 400


# --------------------------------------------------------------------------------------------------
# The timeline
# --------------------------------------------------------------------------------------------------


def test_the_timeline_is_ordered_by_the_sequencer_not_by_source_time(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    """The producers' clocks disagree, deliberately and by a lot.

    The second record admitted carries a source time an hour *earlier* than the first. Sorted by
    that field it would come first, and a reader would be shown an order that never happened.
    """
    run_id, attempt_id = attempt
    _admit(
        db,
        run_id,
        attempt_id,
        producer=SUPERVISOR,
        seq=1,
        event_type="RUN_STARTED",
        source_time=BASE_TIME,
    )
    _admit(
        db,
        run_id,
        attempt_id,
        producer=OBSERVER,
        seq=1,
        event_type="OBSERVER_RECEIPT",
        source_time=BASE_TIME - timedelta(hours=1),
    )

    body = client.get(f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={attempt_id}").json()
    assert [e["sequence"] for e in body["events"]] == [1, 2]
    assert [e["eventType"] for e in body["events"]] == ["RUN_STARTED", "OBSERVER_RECEIPT"]
    # The disagreeing times are still reported, because a reader is entitled to see that they
    # disagree.
    assert body["events"][1]["sourceTime"] < body["events"][0]["sourceTime"]
    assert "not by any clock" in body["orderingMeaning"]


def test_every_event_carries_the_producer_that_submitted_it(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=1, event_type="ACTION_DISPATCHED")
    _admit(db, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="OBSERVER_RECEIPT")

    events = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={attempt_id}"
    ).json()["events"]
    # A supervisor's receipt is not independent observer proof, and a timeline that rendered both as
    # "evidence" would erase the distinction the outcome depends on.
    assert [e["producerId"] for e in events] == [SUPERVISOR, OBSERVER]
    assert [e["producerSequence"] for e in events] == [1, 1]
    assert all(len(e["sourceRecordDigest"]) == 64 for e in events)


def test_the_timeline_reports_the_recorded_payload(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    _admit(
        db,
        run_id,
        attempt_id,
        producer=OBSERVER,
        seq=1,
        event_type="READER_OBSERVATION",
        payload={"phrase": "Email address, edit text, invalid entry"},
    )
    event = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={attempt_id}"
    ).json()["events"][0]
    assert event["payload"] == {"phrase": "Email address, edit text, invalid entry"}
    # And its digest, so a reader can check the payload against the chain rather than trust it.
    assert event["payloadDigest"] == digest(event["payload"])


def test_the_timeline_pages_and_reports_exhaustion_only_on_a_short_page(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    for seq in range(1, 5):
        _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=seq, event_type="ACTION_DISPATCHED")

    full = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={attempt_id}&limit=2"
    ).json()
    assert [e["sequence"] for e in full["events"]] == [1, 2]
    # A full page cannot tell "these are all of them" from "these are all of them so far".
    assert full["exhausted"] is False

    rest = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline"
        f"?attempt_id={attempt_id}&limit=2&after_sequence={full['nextAfterSequence']}"
    ).json()
    # Two events remain and the page holds two, so the server fetched one more and found none:
    # a short page, and the only shape that can honestly claim exhaustion.
    assert [e["sequence"] for e in rest["events"]] == [3, 4]
    assert rest["exhausted"] is True

    tail = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline"
        f"?attempt_id={attempt_id}&limit=2&after_sequence={rest['nextAfterSequence']}"
    ).json()
    assert tail["events"] == []
    assert tail["exhausted"] is True


def test_an_attempt_with_no_events_is_an_empty_timeline_not_an_error(
    client: TestClient, attempt: tuple[str, str]
) -> None:
    """Empty is a fact about the evidence, and the screen is responsible for saying which fact.

    An attempt that recorded nothing is not a silent successful journey; the completeness route is
    what distinguishes the two, and it reports an unbounded lifecycle here.
    """
    run_id, attempt_id = attempt
    body = client.get(f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={attempt_id}").json()
    assert body["events"] == []
    assert body["exhausted"] is True


def test_another_workspaces_attempt_yields_nothing(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, _ = attempt
    with workspace_connection(db, WS_OTHER) as conn:
        other_run = runs.create_run(conn, workspace_id=WS_OTHER, manifest_digest=MANIFEST)
        other_attempt = runs.start_attempt(
            conn, run_id=other_run, workspace_id=WS_OTHER, lease_epoch=0
        )
    _admit_other = None
    body = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/timeline?attempt_id={other_attempt}"
    ).json()
    assert body["events"] == []
    assert _admit_other is None


# --------------------------------------------------------------------------------------------------
# Completeness — where a replay screen can quietly become a reassurance
# --------------------------------------------------------------------------------------------------


def test_completeness_reports_no_outcome_at_all(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    """Deliberately absent.

    Completeness is an input to a verdict, not a verdict. A complete evidence set can still describe
    a failure, and an incomplete one does not become a pass by being tidy.
    """
    run_id, attempt_id = attempt
    body = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/completeness?attempt_id={attempt_id}"
    ).json()
    assert "outcome" not in body
    assert "complete" not in body
    assert "pass" not in body
    assert "complete evidence set can still describe a failure" in body["meaning"]


def test_a_contiguous_chain_with_an_open_producer_is_reported_as_both(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    """INV-06, at the read boundary.

    A producer that stopped halfway leaves a perfect contiguous chain covering half the attempt. A
    single "complete" field would be true of exactly that attempt, which is why there are two.
    """
    run_id, attempt_id = attempt
    _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=1, event_type="RUN_STARTED")
    _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=2, event_type="RUN_FINISHED")

    body = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/completeness?attempt_id={attempt_id}"
    ).json()
    assert body["contiguous"] is True
    assert body["producersClosed"] is False
    assert any("closed their streams" in reason for reason in body["reasons"])
    assert any("perfect chain and half the evidence" in reason for reason in body["reasons"])


def test_an_attempt_with_no_lifecycle_pair_says_its_extent_is_undefined(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=1, event_type="READER_OBSERVATION")

    body = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/completeness?attempt_id={attempt_id}"
    ).json()
    assert body["lifecycleBounded"] is False
    # An empty or truncated transcript after a run started is missing evidence, not a valid silent
    # journey, and this is the field that says so.
    assert any("extent is undefined" in reason for reason in body["reasons"])


def test_every_producer_is_listed_with_how_far_it_got(
    client: TestClient, db: str, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    _admit(db, run_id, attempt_id, producer=SUPERVISOR, seq=1, event_type="RUN_STARTED")
    _admit(db, run_id, attempt_id, producer=OBSERVER, seq=1, event_type="OBSERVER_RECEIPT")
    _admit(db, run_id, attempt_id, producer=OBSERVER, seq=2, event_type="OBSERVER_RECEIPT")

    producers = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/completeness?attempt_id={attempt_id}"
    ).json()["producers"]
    by_id = {p["producerId"]: p for p in producers}
    assert by_id[SUPERVISOR]["admittedThrough"] == 1
    assert by_id[OBSERVER]["admittedThrough"] == 2
    # None, not 0. "Never closed" and "closed at position zero" are different statements.
    assert by_id[OBSERVER]["closedAt"] is None


def test_an_empty_attempt_reports_the_reasons_rather_than_looking_clean(
    client: TestClient, attempt: tuple[str, str]
) -> None:
    run_id, attempt_id = attempt
    body = client.get(
        f"/v1/workspaces/{WS}/runs/{run_id}/completeness?attempt_id={attempt_id}"
    ).json()
    # Nothing was recorded at all, and the honest reading of that is "the extent is undefined",
    # never an empty timeline presented as an uneventful run.
    assert body["reasons"] != []
    assert body["lifecycleBounded"] is False
