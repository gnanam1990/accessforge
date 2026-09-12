"""Adversarial probes against the declared deployment boundaries, through the real HTTP surface.

The existing isolation suites prove the *database* holds the line: row-level security is FORCEd, an
unpredicated query returns nothing, a revoked membership grants nothing. This suite attacks from
where an attacker actually stands — a signed-in member of one workspace, holding a valid session,
sending well-formed requests at every route the API exposes.

The rule under test everywhere here: **an outsider learns nothing, including whether the thing they
asked about exists.** A 403 for a resource in another workspace confirms it exists; so does a 404
that arrives faster, or a different error code, or a different message.

Each probe names the boundary it attacks so a failure says which one gave way. Nothing here is a
substitute for external penetration testing, which has not happened.

Requirements: FR-014, FR-020, FR-021. Invariants: INV-07, INV-08, INV-12.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    projects,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

MINE = str(uuid.UUID(int=0x280))
THEIRS = str(uuid.UUID(int=0x281))
ME = str(uuid.UUID(int=0x282))
THEM = str(uuid.UUID(int=0x283))
MANIFEST = digest({"manifest": "probe"})


@pytest.fixture()
def api_settings(test_database_url: str) -> ApiSettings:
    return ApiSettings(
        database_url=test_database_url,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )


@pytest.fixture()
def world(test_database_url: str) -> Iterator[dict[str, Any]]:
    """Two workspaces, each with an owner, and real records in the one I am not a member of."""
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Mine')", (MINE,))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Theirs')", (THEIRS,))
        for user in (ME, THEM):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
            )
    for workspace, user in ((MINE, ME), (THEIRS, THEM)):
        with workspace_connection(test_database_url, workspace) as conn:
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, 'OWNER')",
                (workspace, user),
            )
            budgets.configure_entitlement(
                conn,
                workspace_id=workspace,
                max_runs_per_day=10,
                max_actions_per_day=1000,
                max_wall_seconds_per_day=3600,
                max_model_tokens_per_day=100_000,
                max_concurrent_runs=5,
                configured_by="fixture",
                reason="probe fixture",
            )

    # Real records in the workspace I am not a member of, so every probe is aimed at something that
    # exists. A probe at a nonexistent id proves only that nothing is nothing.
    with workspace_connection(test_database_url, THEIRS) as conn:
        their_project = projects.create_project(conn, workspace_id=THEIRS, name="Theirs")
        their_run = runs.create_run(conn, workspace_id=THEIRS, manifest_digest=MANIFEST)
        their_attempt = runs.start_attempt(
            conn, run_id=their_run, workspace_id=THEIRS, lease_epoch=0
        )
        sequencer.admit_record(
            conn,
            workspace_id=THEIRS,
            run_id=their_run,
            attempt_id=their_attempt,
            lease_epoch=0,
            producer_id="observer-1",
            source_record_id="secret-1",
            producer_sequence=1,
            event_type="READER_OBSERVATION",
            manifest_digest=MANIFEST,
            payload={"phrase": "their private transcript"},
            source_time=datetime(2026, 9, 10, 12, tzinfo=UTC),
        )
        budgets.record_usage(
            conn,
            workspace_id=THEIRS,
            event_key="their-usage",
            kind="MODEL_TOKENS",
            quantity=99_999,
        )

    yield {
        "db": test_database_url,
        "project": their_project,
        "run": their_run,
        "attempt": their_attempt,
    }


@pytest.fixture()
def client(world: dict[str, Any], api_settings: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(api_settings)) as test_client:
        with workspace_connection(str(world["db"]), MINE) as conn:
            issued = issue_session(conn, user_id=ME)
        test_client.cookies.set(SESSION_COOKIE, issued.session_token)
        test_client.headers[CSRF_HEADER] = issued.csrf_token
        yield test_client


def _probe_paths(world: dict[str, Any]) -> list[tuple[str, str]]:
    """Every read route, aimed at the other workspace's real records.

    Named so a failure says which boundary gave way rather than which line number did.
    """
    run_id = str(world["run"])
    attempt = str(world["attempt"])
    project = str(world["project"])
    return [
        ("projects listing", f"/v1/workspaces/{THEIRS}/projects"),
        ("project detail", f"/v1/workspaces/{THEIRS}/projects/{project}"),
        ("environments", f"/v1/workspaces/{THEIRS}/projects/{project}/environments"),
        ("sealed manifests", f"/v1/workspaces/{THEIRS}/projects/{project}/manifests"),
        ("journey versions", f"/v1/workspaces/{THEIRS}/projects/{project}/journeys"),
        ("journey capabilities", f"/v1/workspaces/{THEIRS}/journey-capabilities"),
        ("runs listing", f"/v1/workspaces/{THEIRS}/runs"),
        ("run detail", f"/v1/workspaces/{THEIRS}/runs/{run_id}"),
        ("attempts", f"/v1/workspaces/{THEIRS}/runs/{run_id}/attempts"),
        ("timeline", f"/v1/workspaces/{THEIRS}/runs/{run_id}/timeline?attempt_id={attempt}"),
        ("canonical events", f"/v1/workspaces/{THEIRS}/runs/{run_id}/events?attempt_id={attempt}"),
        (
            "evidence summary",
            f"/v1/workspaces/{THEIRS}/runs/{run_id}/evidence?attempt_id={attempt}",
        ),
        (
            "completeness",
            f"/v1/workspaces/{THEIRS}/runs/{run_id}/completeness?attempt_id={attempt}",
        ),
        ("runner inventory", f"/v1/workspaces/{THEIRS}/runners"),
        ("findings", f"/v1/workspaces/{THEIRS}/findings"),
        ("review queue", f"/v1/workspaces/{THEIRS}/review-requests"),
        ("members", f"/v1/workspaces/{THEIRS}/members"),
        ("usage", f"/v1/workspaces/{THEIRS}/usage"),
        ("entitlement", f"/v1/workspaces/{THEIRS}/settings/entitlement"),
        ("retention policy", f"/v1/workspaces/{THEIRS}/settings/retention"),
    ]


def test_every_read_route_refuses_a_non_member_identically(
    client: TestClient, world: dict[str, Any]
) -> None:
    """One answer, everywhere, for a member of another workspace.

    A route that answered differently — 403 rather than 404, a different code, a longer message —
    would confirm that the workspace exists and that something is in it. Collected into one test so
    a new route added without this behaviour shows up as a diff on a list rather than as a missing
    test nobody wrote.
    """
    answers: dict[str, tuple[int, str]] = {}
    for name, path in _probe_paths(world):
        response = client.get(path)
        body = response.json()
        answers[name] = (response.status_code, str(body.get("code", "")))

    unexpected = {
        name: answer for name, answer in answers.items() if answer != (404, "RESOURCE_NOT_FOUND")
    }
    assert not unexpected, unexpected


def test_no_refusal_reveals_the_other_workspaces_content(
    client: TestClient, world: dict[str, Any]
) -> None:
    """The refusal body is the same uninformative document everywhere.

    Not merely the same status: a message naming the resource, the workspace or the count of rows
    behind it would be the disclosure the status code was chosen to prevent.
    """
    secrets = ("their private transcript", "Theirs", "observer-1", "99999", str(world["run"]))
    for name, path in _probe_paths(world):
        text = client.get(path).text
        for secret in secrets:
            assert secret not in text, (name, secret)


def test_a_forged_workspace_in_the_body_is_refused_rather_than_ignored(
    client: TestClient,
) -> None:
    """Authority comes from the session and the path. A body field is a claim by the caller."""
    response = client.post(
        f"/v1/workspaces/{MINE}/projects",
        json={"name": "smuggled", "workspaceId": THEIRS},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNEXPECTED_FIELD"


def test_another_workspaces_attempt_cannot_be_read_through_my_own_run(
    client: TestClient, world: dict[str, Any]
) -> None:
    """The composition attack: my run, their attempt.

    Both identifiers are individually legitimate — one is mine, one exists — and a route that
    validated each separately would answer.
    """
    with workspace_connection(str(world["db"]), MINE) as conn:
        my_run = runs.create_run(conn, workspace_id=MINE, manifest_digest=MANIFEST)

    for path in (
        f"/v1/workspaces/{MINE}/runs/{my_run}/timeline?attempt_id={world['attempt']}",
        f"/v1/workspaces/{MINE}/runs/{my_run}/completeness?attempt_id={world['attempt']}",
        f"/v1/workspaces/{MINE}/runs/{my_run}/events?attempt_id={world['attempt']}",
        f"/v1/workspaces/{MINE}/runs/{my_run}/evidence?attempt_id={world['attempt']}",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "their private transcript" not in response.text


def test_a_run_cannot_be_requested_into_another_workspace(client: TestClient) -> None:
    response = client.post(f"/v1/workspaces/{THEIRS}/runs", json={"manifestDigest": MANIFEST})
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_another_workspaces_usage_is_not_charged_to_mine(client: TestClient) -> None:
    """Their 99,999 tokens must not appear against my allowance.

    If they did, my limit would be theirs to exhaust.
    """
    body = client.get(f"/v1/workspaces/{MINE}/usage").json()
    tokens = next(row for row in body["usage"] if row["kind"] == "MODEL_TOKENS")
    assert tokens["measured"] == 0
    assert tokens["countedAgainstLimit"] == 0


def test_a_revoked_membership_stops_working_on_the_next_request(
    client: TestClient, world: dict[str, Any]
) -> None:
    """Not on the next sign-in.

    A cached authorization decision is an authorization nobody can revoke.
    """
    assert client.get(f"/v1/workspaces/{MINE}/projects").status_code == 200
    with workspace_connection(str(world["db"]), MINE) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() "
            "WHERE workspace_id = %s AND user_id = %s",
            (MINE, ME),
        )
    after = client.get(f"/v1/workspaces/{MINE}/projects")
    assert after.status_code == 404
    # The same answer an outsider gets, so a revoked member cannot tell revocation from deletion.
    assert after.json()["code"] == "RESOURCE_NOT_FOUND"


def test_a_revoked_session_stops_working_on_the_next_request(
    client: TestClient, world: dict[str, Any]
) -> None:
    assert client.get(f"/v1/workspaces/{MINE}/projects").status_code == 200
    with unscoped_connection(str(world["db"])) as conn:
        conn.execute("UPDATE user_session SET revoked_at = now() WHERE user_id = %s", (ME,))
    assert client.get(f"/v1/workspaces/{MINE}/projects").status_code == 401


def test_no_response_anywhere_carries_a_credential_or_a_stack_trace(
    client: TestClient, world: dict[str, Any]
) -> None:
    """A canary in every capture path this suite can reach.

    The session token and the CSRF token are held by this client; a response echoing either would
    turn any read-only disclosure into account takeover. `psycopg`, a file path or a traceback would
    mean an exception message reached a caller.
    """
    token = next(c.value for c in client.cookies.jar if c.name == SESSION_COOKIE)
    assert token is not None
    csrf = client.headers[CSRF_HEADER]
    paths = [path for _, path in _probe_paths(world)] + [
        f"/v1/workspaces/{MINE}/projects",
        f"/v1/workspaces/{MINE}/usage",
        f"/v1/workspaces/{MINE}/settings/retention",
        "/diagnostics",
        "/health/ready",
    ]
    for path in paths:
        text = client.get(path).text
        for canary in (token, csrf, "Traceback", "psycopg", "/Users/", "password="):
            assert canary not in text, (path, canary)


# There is no probe here for enrollment tokens being unreadable. `test_http_api.py` already walks
# the whole route table and asserts no path containing `enrollment-token` exposes anything but POST,
# and a second weaker copy of that check — this one reached for `client.app.routes` and found an
# empty list — would be a test that passes for the wrong reason.


def test_diagnostics_never_reports_a_credential(client: TestClient) -> None:
    body = client.get("/diagnostics").json()["config"]
    assert body["evidence_access_key"] == "<redacted>"
    assert body["evidence_secret_key"] == "<redacted>"
    assert "<redacted>" in body["database"]
