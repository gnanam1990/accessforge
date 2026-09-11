"""Usage, entitlements and retention through the real API.

Two themes. The first is that a limit gates real work: a run request is charged against the
workspace's allowance in the same transaction that creates it, and a workspace with no allowance is
refused rather than treated as unlimited. The second is that no surface here reports a cost — R1
measures usage and enforces a limit somebody set, and collects no money.

Requirements: FR-014, FR-020, FR-021, FR-025. Invariants: INV-07, INV-08, INV-15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x270))
OWNER = str(uuid.UUID(int=0x271))
VIEWER = str(uuid.UUID(int=0x272))


@pytest.fixture()
def settings_object(test_database_url: str) -> ApiSettings:
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
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Budgeted')", (WS,))
        for user in (OWNER, VIEWER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
            )
    with workspace_connection(test_database_url, WS) as conn:
        for user, role in ((OWNER, "OWNER"), (VIEWER, "VIEWER")):
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, %s)",
                (WS, user, role),
            )
    yield test_database_url


@pytest.fixture()
def manifest(db: str, seal_manifest: Callable[..., str]) -> str:
    """A manifest this workspace has genuinely sealed.

    `POST /runs` refuses a digest nothing sealed, so a test that wants to request a run has to do
    what a caller does. The module constant this replaced was a bare `digest({...})` the route
    accepted on trust — which was the defect, not the test.
    """

    from accessforge_persistence import projects as project_store

    with workspace_connection(db, WS) as conn:
        project_id = project_store.create_project(conn, workspace_id=WS, name="sealed")
    return seal_manifest(db, workspace_id=WS, project_id=project_id, authorized_by=OWNER)


@pytest.fixture()
def client(db: str, settings_object: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings_object)) as test_client:
        yield test_client


def _sign_in(db: str, client: TestClient, user_id: str = OWNER) -> dict[str, str]:
    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return {CSRF_HEADER: issued.csrf_token}


def _entitlement_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "maxRunsPerDay": 5,
        "maxActionsPerDay": 500,
        "maxWallSecondsPerDay": 3600,
        "maxModelTokensPerDay": 100_000,
        "maxConcurrentRuns": 2,
        "reason": "initial allowance for the pilot",
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------------------------------
# A limit that gates real work
# --------------------------------------------------------------------------------------------------


def test_a_run_cannot_be_requested_before_anyone_configures_an_allowance(
    client: TestClient, db: str, manifest: str
) -> None:
    headers = _sign_in(db, client)
    response = client.post(
        f"/v1/workspaces/{WS}/runs", json={"manifestDigest": manifest}, headers=headers
    )
    # Not 202, and not a generous default. A workspace nobody configured is refused, because
    # treating it as unlimited would make "undecided" and "permitted anything" the same state.
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "An administrator configures it" in response.json()["detail"]


def test_requesting_runs_consumes_the_allowance_and_is_then_refused(
    client: TestClient, db: str, manifest: str
) -> None:
    headers = _sign_in(db, client)
    # Concurrency deliberately generous: this test is about the daily allowance, and a low
    # concurrency limit would refuse the third run for the other reason and prove nothing about
    # this one.
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=2, maxConcurrentRuns=50),
        headers={**headers, "If-Match": "0"},
    )

    for index in range(2):
        accepted = client.post(
            f"/v1/workspaces/{WS}/runs",
            json={"manifestDigest": manifest},
            headers={**headers, "Idempotency-Key": f"run-{index}"},
        )
        assert accepted.status_code == 202, accepted.text

    refused = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        headers={**headers, "Idempotency-Key": "run-2"},
    )
    assert refused.status_code == 429
    problem = refused.json()
    assert problem["code"] == "QUOTA_EXHAUSTED"
    # Which limit, and how much of it is gone. "Quota exceeded" tells an operator nothing about
    # what to change.
    assert problem["kind"] == "RUN_ADMITTED"
    assert problem["limit"] == 2
    assert problem["used"] == 2


def test_an_idempotent_retry_is_not_charged_twice(
    client: TestClient, db: str, manifest: str
) -> None:
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=1, maxConcurrentRuns=50),
        headers={**headers, "If-Match": "0"},
    )
    body = {"manifestDigest": manifest}
    first = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, headers={**headers, "Idempotency-Key": "same"}
    )
    second = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, headers={**headers, "Idempotency-Key": "same"}
    )
    assert first.status_code == 202
    # The retry replays rather than being refused for exhausting a limit its own first attempt used.
    assert second.status_code == 202
    assert second.headers.get("Idempotent-Replay") == "true"

    usage = client.get(f"/v1/workspaces/{WS}/usage").json()
    runs = next(u for u in usage["usage"] if u["kind"] == "RUN_ADMITTED")
    assert runs["countedAgainstLimit"] == 1


# --------------------------------------------------------------------------------------------------
# What usage reports, and what it never reports
# --------------------------------------------------------------------------------------------------


def test_usage_separates_measured_estimated_and_unavailable(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    with workspace_connection(db, WS) as conn:
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="m",
            kind="MODEL_TOKENS",
            quantity=1_000,
            basis="MEASURED",
        )
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="e",
            kind="MODEL_TOKENS",
            quantity=2_500,
            basis="ESTIMATED",
        )
        budgets.record_usage(
            conn,
            workspace_id=WS,
            event_key="u",
            kind="MODEL_TOKENS",
            quantity=0,
            basis="UNAVAILABLE",
        )

    tokens = next(
        u
        for u in client.get(f"/v1/workspaces/{WS}/usage").json()["usage"]
        if u["kind"] == "MODEL_TOKENS"
    )
    assert tokens["measured"] == 1_000
    assert tokens["estimated"] == 2_500
    assert tokens["unavailableEvents"] == 1
    assert tokens["countedAgainstLimit"] == 3_500
    assert tokens["remaining"] == 100_000 - 3_500


def test_no_usage_response_reports_a_cost(client: TestClient, db: str) -> None:
    """R1 measures usage and enforces a limit. It collects no money and computes no saving.

    Asserted on the response's *field names* rather than its prose: the response says in words that
    nothing here is a cost, and a text search would flag that sentence while missing a field called
    `spend`. The thing that would actually go wrong is a field added later for a dashboard.
    """
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    body = client.get(f"/v1/workspaces/{WS}/usage").json()

    def field_names(value: Any) -> set[str]:
        if isinstance(value, dict):
            names = set(value.keys())
            for nested in value.values():
                names |= field_names(nested)
            return names
        if isinstance(value, list):
            return {name for item in value for name in field_names(item)}
        return set()

    forbidden = {
        "price",
        "cost",
        "currency",
        "amount",
        "spend",
        "charge",
        "invoice",
        "saving",
        "savings",
        "balance",
        "credits",
    }
    present = {name for name in field_names(body) if name.lower() in forbidden}
    assert not present, sorted(present)
    # And no currency symbol or code appears in any value.
    assert not any(
        symbol in client.get(f"/v1/workspaces/{WS}/usage").text for symbol in ("$", "€", "£")
    )


def test_usage_says_what_kind_of_number_each_column_is(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    meaning = client.get(f"/v1/workspaces/{WS}/usage").json()["meaning"]
    assert "reported about its own consumption" in meaning
    assert "it is not zero usage" in meaning


# --------------------------------------------------------------------------------------------------
# Configuring, and who may
# --------------------------------------------------------------------------------------------------


def test_configuring_appends_a_revision(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    first = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    assert first.status_code == 201
    assert first.json()["revision"] == 1

    second = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=9),
        headers={**headers, "If-Match": "1"},
    )
    assert second.json()["revision"] == 2
    # The previous revision is unchanged, so an admission decided under it stays explainable.
    assert "previous one is unchanged" in second.json()["meaning"]


def test_a_stale_revision_is_refused(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    response = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=9),
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 409
    assert "Somebody changed it while you were deciding" in response.json()["detail"]


def test_configuring_without_if_match_is_refused(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement", json=_entitlement_body(), headers=headers
    )
    # Changing a limit is a decision about the one currently in force, and a caller who did not
    # read it is deciding about nothing.
    assert response.status_code == 428


def test_a_limit_must_say_why_it_is_what_it_is(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(reason="  "),
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 400
    assert response.json()["field"] == "reason"


def test_there_is_no_value_meaning_unlimited(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    for value in (-1, None, "many"):
        response = client.put(
            f"/v1/workspaces/{WS}/settings/entitlement",
            json=_entitlement_body(maxRunsPerDay=value),
            headers={**headers, "If-Match": "0"},
        )
        assert response.status_code == 400, value


def test_a_run_request_key_cannot_be_reused_to_admit_a_second_run_for_free(
    client: TestClient, db: str, manifest: str
) -> None:
    """The usage event key is namespaced by route.

    A caller chooses their own idempotency key. If two routes shared one key space, a key already
    used elsewhere would read as a retry of this admission, and the run would be created without
    being charged.
    """
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=1, maxConcurrentRuns=5),
        headers={**headers, "If-Match": "0"},
    )
    with workspace_connection(db, WS) as conn:
        # A usage event recorded under the bare key an idempotency header would carry.
        budgets.record_usage(
            conn, workspace_id=WS, event_key="shared-key", kind="RUN_ADMITTED", quantity=1
        )

    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        headers={**headers, "Idempotency-Key": "shared-key"},
    )
    # Refused, because the allowance of one is already spent and this key is not the key that spent
    # it. Sharing the key space would have admitted this run for free.
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "QUOTA_EXHAUSTED"


def test_concurrency_is_refused_with_its_own_explanation(
    client: TestClient, db: str, manifest: str
) -> None:
    headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(maxRunsPerDay=100, maxConcurrentRuns=1),
        headers={**headers, "If-Match": "0"},
    )
    first = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        headers={**headers, "Idempotency-Key": "one"},
    )
    assert first.status_code == 202

    second = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        headers={**headers, "Idempotency-Key": "two"},
    )
    assert second.status_code == 429
    problem = second.json()
    assert problem["kind"] == "CONCURRENT_RUNS"
    # A different remedy from a daily allowance: this clears as runs finish, and telling somebody to
    # raise a limit that is nowhere near exhausted wastes their time.
    assert "clears as runs finish" in problem["detail"]


def test_a_viewer_cannot_change_a_limit(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client, VIEWER)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 403


def test_a_viewer_may_read_usage(client: TestClient, db: str) -> None:
    owner_headers = _sign_in(db, client)
    client.put(
        f"/v1/workspaces/{WS}/settings/entitlement",
        json=_entitlement_body(),
        headers={**owner_headers, "If-Match": "0"},
    )
    _sign_in(db, client, VIEWER)
    # Read-only is a real view, not an error. Knowing what the limit is does not change it.
    assert client.get(f"/v1/workspaces/{WS}/usage").status_code == 200


# --------------------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------------------


def test_defaults_apply_and_say_they_are_defaults(client: TestClient, db: str) -> None:
    _sign_in(db, client)
    body = client.get(f"/v1/workspaces/{WS}/settings/retention").json()
    # Revision 0 distinguishes "nobody has chosen" from "somebody chose this".
    assert body["revision"] == 0
    classes = {c["evidenceClass"] for c in body["classes"]}
    assert "READER_SPEECH" in classes
    assert "MODEL_EXCHANGE" in classes


def test_every_class_says_whether_deleting_it_breaks_a_completeness_claim(
    client: TestClient, db: str
) -> None:
    _sign_in(db, client)
    classes = client.get(f"/v1/workspaces/{WS}/settings/retention").json()["classes"]
    by_name = {c["evidenceClass"]: c for c in classes}
    # Reader speech is what a reader assertion is decided from; deleting it makes those assertions
    # unsupported. A screen recording is supplementary and never the basis of a verdict.
    assert by_name["READER_SPEECH"]["invalidatesCompleteness"] is True
    assert by_name["SCREEN_RECORDING"]["invalidatesCompleteness"] is False
    assert all("meaning" in c and c["meaning"] for c in classes)


def test_the_limits_of_deletion_are_stated(client: TestClient, db: str) -> None:
    _sign_in(db, client)
    limits = " ".join(client.get(f"/v1/workspaces/{WS}/settings/retention").json()["limits"])
    assert "invalidates any completeness claim" in limits
    # The one nobody expects: a downloaded copy is beyond reach.
    assert "already downloaded still contains" in limits
    assert "Backups expire" in limits


def test_a_partial_retention_revision_is_refused(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/retention",
        json={"classes": [{"evidenceClass": "READER_SPEECH", "retainDays": 7}]},
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 400
    # A partial revision leaves the unlisted classes at whatever the last one said, and a reader
    # cannot tell a decision from a leftover.
    assert "cannot tell a decision from a leftover" in response.json()["detail"]


def test_configuring_retention_appends_a_revision(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    every = [
        {"evidenceClass": name, "retainDays": 7, "consentRequired": True}
        for name in (
            "SOURCE_SNAPSHOT",
            "FIXTURE_REFERENCE",
            "READER_SPEECH",
            "SCREEN_RECORDING",
            "DIAGNOSTIC",
            "MODEL_EXCHANGE",
            "REVIEW_RECORD",
        )
    ]
    created = client.put(
        f"/v1/workspaces/{WS}/settings/retention",
        json={"classes": every},
        headers={**headers, "If-Match": "0"},
    )
    assert created.status_code == 201
    assert created.json()["revision"] == 1

    body = client.get(f"/v1/workspaces/{WS}/settings/retention").json()
    assert body["revision"] == 1
    assert all(c["retainDays"] == 7 for c in body["classes"])
    # Still true, and still not configurable: whether deletion breaks a completeness claim is a
    # property of what the class is, not of what an administrator would prefer.
    by_name = {c["evidenceClass"]: c for c in body["classes"]}
    assert by_name["READER_SPEECH"]["invalidatesCompleteness"] is True


def test_a_viewer_cannot_change_retention(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client, VIEWER)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/retention",
        json={"classes": []},
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 403


def test_an_unknown_evidence_class_is_refused(client: TestClient, db: str) -> None:
    headers = _sign_in(db, client)
    response = client.put(
        f"/v1/workspaces/{WS}/settings/retention",
        json={"classes": [{"evidenceClass": "EVERYTHING", "retainDays": 1}]},
        headers={**headers, "If-Match": "0"},
    )
    assert response.status_code == 400
    assert "is not an evidence class" in response.json()["detail"]
