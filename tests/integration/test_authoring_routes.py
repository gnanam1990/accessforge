"""Freezing journeys, authorizing environments and reading the runner inventory.

These are the routes module 22's screens depend on, and each exists because the alternative was for
the UI to invent something. The tests are mostly about the refusals: a journey that asks for an
unsupported capability, an environment whose observer and reset credentials are the same identity,
and a runner status that must never be inferred from a process being reachable.

Requirements: FR-001, FR-002, FR-003, FR-004, FR-005. Invariants: INV-02, INV-03, INV-05, INV-07.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x220))
WS_OTHER = str(uuid.UUID(int=0x221))
MAINTAINER = str(uuid.UUID(int=0x222))
VIEWER = str(uuid.UUID(int=0x223))


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
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        for user in (MAINTAINER, VIEWER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
            )
    with workspace_connection(test_database_url, WS) as conn:
        for user, role in ((MAINTAINER, "MAINTAINER"), (VIEWER, "VIEWER")):
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, %s)",
                (WS, user, role),
            )
    yield test_database_url


@pytest.fixture()
def client(db: str, settings: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _signed_in(db: str, client: TestClient, user_id: str = MAINTAINER) -> dict[str, str]:
    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return {CSRF_HEADER: issued.csrf_token}


def _project(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        f"/v1/workspaces/{WS}/projects", json={"name": "Reference app"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return str(response.json()["projectId"])


def _draft(project_id: str, **overrides: Any) -> dict[str, Any]:
    """A journey that the domain accepts, so a test can break exactly one thing at a time."""
    body: dict[str, Any] = {
        "projectId": project_id,
        "name": "Recover from a form error",
        "platform": "darwin",
        "intent": {
            "summary": "Submit the contact form and recover from the validation error it reports",
            "startUrl": "https://localhost:8443/contact",
            "successCondition": "The form reports it was received",
        },
        "assertions": [
            {
                "assertionId": "task-complete",
                "kind": "TASK_COMPLETION",
                "description": "The application recorded exactly one submission for this fixture",
                "required": True,
                "unknownReasons": ["OBSERVER_UNREACHABLE", "OBSERVATION_MISSING"],
            },
            {
                "assertionId": "error-announced",
                "kind": "REQUIRED_ANNOUNCEMENT",
                "description": "The reader announced the validation error when focus reached it",
                "required": True,
                "unknownReasons": ["READER_UNAVAILABLE", "AMBIGUOUS_LANGUAGE"],
            },
        ],
        "fixture": {
            "templateId": "contact-form",
            "navigatorValues": {"fullName": "Rowan Vale", "message": "Hello"},
            "resetValues": {"seed": "empty"},
            "observerConfig": {"expectedSubmissions": "1"},
        },
        "budget": {"maxActions": 40, "wallTimeSeconds": 300},
        "allowedActions": ["NEXT", "ACTIVATE", "TYPE_TEXT", "KEY_CHORD", "READ_CURRENT"],
        "allowedKeyChords": ["TAB", "ENTER"],
        "allowedEffects": ["FIXTURE_SUBMIT"],
    }
    body.update(overrides)
    return body


def _environment(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Local",
        "allowedOrigins": ["https://localhost:8443"],
        "fixtureResetStrategy": "TRUNCATE_AND_SEED",
        "observerCredentialRef": "vault://observer",
        "resetCredentialRef": "vault://reset",
        "permittedEffects": ["FIXTURE_SUBMIT", "FIXTURE_RESET"],
        "expiresAt": "2027-01-01T00:00:00Z",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------------------------------
# Freezing a journey version
# --------------------------------------------------------------------------------------------------


def test_freezing_returns_the_digests_that_were_actually_sealed(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    response = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    for key in ("journeyDigest", "assertionSetDigest", "fixtureDigest", "navigatorPolicyDigest"):
        assert len(body[key]) == 64, key
    assert "authorizes nothing to run" in body["meaning"]


def test_freezing_the_same_draft_twice_produces_the_same_journey_digest(
    client: TestClient, db: str
) -> None:
    """Two versions, one digest. The digest is content, not identity.

    A digest that varied between identical drafts would make every seal comparison meaningless, and
    a run's manifest is a comparison of exactly these values.
    """
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    first = client.post(f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers)
    second = client.post(f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers)
    assert first.json()["journeyDigest"] == second.json()["journeyDigest"]
    assert first.json()["journeyVersionId"] != second.json()["journeyVersionId"]


def test_a_frozen_version_cannot_be_edited(client: TestClient, db: str) -> None:
    """Structural, not behavioural: there is no route that edits one."""
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    version = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers
    ).json()["journeyVersionId"]

    for method in ("PATCH", "PUT", "DELETE"):
        response = client.request(
            method, f"/v1/workspaces/{WS}/journeys/{version}", json={}, headers=headers
        )
        assert response.status_code == 405, (method, response.status_code)


def test_a_successor_records_what_it_replaces(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    original = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers
    ).json()

    changed = _draft(project_id, supersedes=original["journeyVersionId"])
    changed["budget"] = {"maxActions": 60, "wallTimeSeconds": 300}
    second = client.post(f"/v1/workspaces/{WS}/journeys", json=changed, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["supersedes"] == original["journeyVersionId"]
    # The two *digests*, not a digest against an identifier. Comparing the digest to the version id
    # was an assertion that could never fail, so it said nothing about whether the budget change
    # actually changed the seal — which is the property it was written to check.
    assert second.json()["journeyDigest"] != original["journeyDigest"]


def test_a_listing_marks_a_version_that_has_been_superseded(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    first = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers
    ).json()["journeyVersionId"]
    changed = _draft(project_id, supersedes=first)
    changed["budget"] = {"maxActions": 60, "wallTimeSeconds": 300}
    second = client.post(f"/v1/workspaces/{WS}/journeys", json=changed, headers=headers).json()[
        "journeyVersionId"
    ]

    items = client.get(f"/v1/workspaces/{WS}/projects/{project_id}/journeys").json()["items"]
    by_id = {item["journeyVersionId"]: item for item in items}
    # Current is what an operator runs. A superseded version that reported no successor would be
    # offered as current, which is the one thing this field exists to prevent.
    assert by_id[first]["supersededBy"] == second
    assert by_id[second]["supersededBy"] is None


def test_a_successor_cannot_name_a_version_from_another_project(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    first_project = _project(client, headers)
    other_project = str(
        client.post(
            f"/v1/workspaces/{WS}/projects", json={"name": "Another"}, headers=headers
        ).json()["projectId"]
    )
    version = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(first_project), headers=headers
    ).json()["journeyVersionId"]

    response = client.post(
        f"/v1/workspaces/{WS}/journeys",
        json=_draft(other_project, supersedes=version),
        headers=headers,
    )
    assert response.status_code == 400
    assert "same project" in response.json()["detail"]


# --------------------------------------------------------------------------------------------------
# Refusals, and which kind each one is
# --------------------------------------------------------------------------------------------------


def test_an_unsupported_action_is_422_with_the_capability_code(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id, allowedActions=["NEXT", "OPEN_TERMINAL"])
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)

    # 422, not 400. The request is well formed and the platform cannot do what it asks, which sends
    # the author somewhere entirely different from a typing mistake.
    assert response.status_code == 422, response.text
    problem = response.json()
    assert problem["code"] == "UNSUPPORTED_CAPABILITY"
    assert problem["capabilityCode"] == "UNSUPPORTED_ACTION"


def test_a_key_chord_that_reaches_the_operating_system_is_refused(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id, allowedKeyChords=["TAB", "CMD+Q"])
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["capabilityCode"] == "FORBIDDEN_KEY_CHORD"


def test_an_external_effect_is_refused(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id, allowedEffects=["FIXTURE_SUBMIT", "SEND_EMAIL"])
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["capabilityCode"] == "FORBIDDEN_EFFECT"


def test_a_malformed_field_is_400_and_names_the_field(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id)
    body["intent"] = {"summary": "", "startUrl": "https://x.test", "successCondition": "done"}
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)

    assert response.status_code == 400
    # A form cannot link an error to a control without this, and UI-UX section 4 requires the link.
    assert response.json()["field"] == "intent"


def test_a_budget_beyond_the_ceiling_names_the_budget_field(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id)
    body["budget"] = {"maxActions": 100000, "wallTimeSeconds": 300}
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "budget"


def test_a_selector_in_the_task_summary_is_refused(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id)
    body["intent"]["summary"] = "Click #submit-button and then read the message"
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "intent"


def test_an_oracle_value_offered_as_navigator_input_is_refused(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id)
    body["fixture"]["navigatorValues"]["expectedSubmissions"] = "1"
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "fixture"


def test_a_journey_with_no_completion_assertion_is_refused(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id)
    body["assertions"] = [body["assertions"][1]]
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "assertions"


def test_a_scalar_of_the_wrong_type_is_refused_rather_than_coerced(
    client: TestClient, db: str
) -> None:
    """`str()`, `bool()` and `int()` all accepted values that changed meaning.

    `null` became the string "None"; the string "false" became True; 1.9 became 1. A journey version
    is immutable once frozen, so a coerced value is coerced forever, and its digest is the digest of
    something the author never submitted.
    """
    headers = _signed_in(db, client)
    project_id = _project(client, headers)

    null_summary = _draft(project_id)
    null_summary["intent"]["summary"] = None
    assert (
        client.post(f"/v1/workspaces/{WS}/journeys", json=null_summary, headers=headers).status_code
        == 400
    )

    string_flag = _draft(project_id)
    string_flag["assertions"][1]["required"] = "false"
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=string_flag, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "assertions[1]"

    fractional_budget = _draft(project_id)
    fractional_budget["budget"] = {"maxActions": 1.9, "wallTimeSeconds": 300}
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=fractional_budget, headers=headers)
    assert response.status_code == 400
    assert response.json()["field"] == "budget"


def test_a_supersession_failure_names_the_supersedes_field(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id, supersedes=str(uuid.uuid4()))
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    assert response.status_code == 400
    # Not `projectId`. A message about the predecessor pointing at the project field sends the
    # operator to the one control that is not the problem.
    assert response.json()["field"] == "supersedes"


def test_a_malformed_identifier_is_refused_before_it_reaches_the_database(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = _draft(project_id, supersedes="not-a-uuid")
    response = client.post(f"/v1/workspaces/{WS}/journeys", json=body, headers=headers)
    # PostgreSQL raises `invalid input syntax for type uuid` on this, which surfaces as an unhandled
    # driver error and a 500. These identifiers arrive from a request body.
    assert response.status_code == 400
    assert response.json()["field"] == "supersedes"


def test_a_malformed_page_cursor_is_refused_rather_than_erroring(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    response = client.get(
        f"/v1/workspaces/{WS}/projects/{project_id}/journeys?after=not-a-uuid", headers=headers
    )
    assert response.status_code == 400


def test_a_disabled_account_cannot_authorize_a_repository(client: TestClient, db: str) -> None:
    """A membership row survives its account being disabled.

    The member listing already excludes disabled accounts, so without the join the API refused to
    *offer* a person it would then happily accept.
    """
    headers = _signed_in(db, client)
    with unscoped_connection(db) as conn:
        conn.execute("UPDATE app_user SET disabled_at = now() WHERE id = %s", (VIEWER,))
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={
            "name": "Reference app",
            "repositoryUrl": "https://github.test/acme/refapp",
            "repositoryAuthorizedBy": VIEWER,
        },
        headers=headers,
    )
    assert response.status_code == 400
    assert "not an active member" in response.json()["detail"]


def test_the_environment_listing_pages_rather_than_truncating(client: TestClient, db: str) -> None:
    """An earlier version took the first 200 with no cursor and no indication that it had stopped.

    A project with more than that lost the older ones silently, including ones still usable, and the
    screen presented what remained as the complete inventory.
    """
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    for index in range(3):
        created = client.post(
            f"/v1/workspaces/{WS}/projects/{project_id}/environments",
            json=_environment(name=f"env-{index}"),
            headers=headers,
        )
        assert created.status_code == 201, created.text

    first = client.get(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments?limit=2", headers=headers
    ).json()
    assert len(first["items"]) == 2
    assert first["nextCursor"] is not None

    second = client.get(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments"
        f"?limit=2&after={first['nextCursor']}",
        headers=headers,
    ).json()
    assert len(second["items"]) == 1
    assert second["nextCursor"] is None
    names = [item["name"] for item in first["items"] + second["items"]]
    assert sorted(names) == ["env-0", "env-1", "env-2"]


def test_a_project_with_no_sealed_manifest_lists_none(client: TestClient, db: str) -> None:
    """The answer a run request has to respect.

    A run is requested against a sealed manifest digest. With none sealed, the honest answer is an
    empty list, and the screen that reads it must refuse to request a run rather than substitute a
    journey digest — which names nothing the dispatcher can match.
    """
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    body = client.get(f"/v1/workspaces/{WS}/projects/{project_id}/manifests").json()
    assert body["items"] == []
    assert "a journey digest alone is not a manifest" in body["meaning"]


def test_a_viewer_cannot_freeze_a_journey(client: TestClient, db: str) -> None:
    maintainer_headers = _signed_in(db, client)
    project_id = _project(client, maintainer_headers)
    viewer_headers = _signed_in(db, client, VIEWER)
    response = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=viewer_headers
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_a_project_in_another_workspace_cannot_receive_a_journey(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    foreign_project = str(uuid.uuid4())
    with workspace_connection(db, WS_OTHER) as conn:
        conn.execute(
            "INSERT INTO project (id, workspace_id, name) VALUES (%s, %s, 'Theirs')",
            (foreign_project, WS_OTHER),
        )
    response = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(foreign_project), headers=headers
    )
    assert response.status_code == 400
    assert response.json()["field"] == "projectId"


# --------------------------------------------------------------------------------------------------
# What may be authored, and what the navigator will see
# --------------------------------------------------------------------------------------------------


def test_the_capability_route_reports_the_domains_own_allowlists(
    client: TestClient, db: str
) -> None:
    _signed_in(db, client)
    body = client.get(f"/v1/workspaces/{WS}/journey-capabilities").json()
    # Served rather than duplicated in the client: a client-side copy is a second definition of the
    # policy, and an author offered a control the server refuses is being invited to fail.
    assert "TYPE_TEXT" in body["allowedActions"]
    assert "CTRL+OPT+RIGHT" in body["allowedKeyChordsByPlatform"]["darwin"]
    assert body["allowedEffects"] == ["FIXTURE_SUBMIT", "FIXTURE_RESET"]
    assert body["maxActions"] > 0


def test_the_navigator_policy_contains_no_oracle_material(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    version = client.post(
        f"/v1/workspaces/{WS}/journeys", json=_draft(project_id), headers=headers
    ).json()["journeyVersionId"]

    response = client.get(f"/v1/workspaces/{WS}/journeys/{version}/policy")
    assert response.status_code == 200
    policy = response.json()["navigatorPolicy"]
    # The oracle key and its value must both be absent. Showing the policy is how the claim that the
    # boundary holds becomes checkable by a person rather than asserted in a document.
    assert "expectedSubmissions" not in response.text
    assert policy["fixtureValues"] == {"fullName": "Rowan Vale", "message": "Hello"}
    assert "DOM" in policy["forbiddenObservations"]


# --------------------------------------------------------------------------------------------------
# Environments
# --------------------------------------------------------------------------------------------------


def test_registering_an_environment_records_the_authorized_scope(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments",
        json=_environment(),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["configDigest"]) == 64
    assert "not a connection test" in response.json()["meaning"]


def test_one_identity_cannot_both_reset_state_and_attest_to_it(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments",
        json=_environment(observerCredentialRef="vault://same", resetCredentialRef="vault://same"),
        headers=headers,
    )
    assert response.status_code == 400
    assert "independent observer" in response.json()["detail"]


def test_an_environment_with_no_origin_is_refused_and_says_why(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments",
        json=_environment(allowedOrigins=[]),
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["field"] == "allowedOrigins"
    # Entering a URL is not a claim that you may test it, and the refusal says so.
    assert "not a claim that you may test it" in response.json()["detail"]


def test_a_listing_never_reveals_a_credential_reference(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    client.post(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments",
        json=_environment(),
        headers=headers,
    )
    response = client.get(f"/v1/workspaces/{WS}/projects/{project_id}/environments")
    assert response.status_code == 200
    assert "vault://" not in response.text
    assert response.json()["items"][0]["allowedOrigins"] == ["https://localhost:8443"]


def test_an_expired_environment_reports_expiry_rather_than_a_bare_unusable(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    project_id = _project(client, headers)
    client.post(
        f"/v1/workspaces/{WS}/projects/{project_id}/environments",
        json=_environment(expiresAt="2020-01-01T00:00:00Z"),
        headers=headers,
    )
    item = client.get(f"/v1/workspaces/{WS}/projects/{project_id}/environments").json()["items"][0]
    # "Expired", "revoked" and "superseded" send an operator to three different actions, and a
    # single false would send them to none of them.
    assert item["expired"] is True
    assert item["revoked"] is False
    assert item["supersededBy"] is None
    assert item["usable"] is False


# --------------------------------------------------------------------------------------------------
# Naming the person who authorized something
# --------------------------------------------------------------------------------------------------


def test_a_repository_authorizer_given_as_a_name_is_refused_with_a_400(
    client: TestClient, db: str
) -> None:
    """Not a 500.

    The column is a UUID referencing a real person, and a name reached the driver as a raw string:
    an unhandled error, a stack trace in the log, and nothing useful for the caller. A real browser
    found this by having a person type their name into the field, which is what the field asked for.
    """
    headers = _signed_in(db, client)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={
            "name": "Reference app",
            "repositoryUrl": "https://github.test/acme/refapp",
            "repositoryAuthorizedBy": "Rowan Vale",
        },
        headers=headers,
    )
    assert response.status_code == 400, response.text
    assert "user id, not by their name" in response.json()["detail"]


def test_an_authorizer_who_is_not_a_member_here_is_refused(client: TestClient, db: str) -> None:
    headers = _signed_in(db, client)
    outsider = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'outsider@example.test')", (outsider,)
        )
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={
            "name": "Reference app",
            "repositoryUrl": "https://github.test/acme/refapp",
            "repositoryAuthorizedBy": outsider,
        },
        headers=headers,
    )
    assert response.status_code == 400
    # An authorization recorded against someone this workspace cannot name is not auditable.
    assert "not an active member" in response.json()["detail"]


def test_a_repository_with_a_real_member_as_authorizer_is_recorded(
    client: TestClient, db: str
) -> None:
    headers = _signed_in(db, client)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={
            "name": "Reference app",
            "repositoryUrl": "https://github.test/acme/refapp",
            "repositoryAuthorizedBy": MAINTAINER,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


def test_the_member_list_covers_this_workspace_only(client: TestClient, db: str) -> None:
    _signed_in(db, client)
    body = client.get(f"/v1/workspaces/{WS}/members").json()
    assert sorted(item["userId"] for item in body["items"]) == sorted([MAINTAINER, VIEWER])


def test_a_revoked_member_is_absent_from_the_list(client: TestClient, db: str) -> None:
    _signed_in(db, client)
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() "
            "WHERE workspace_id = %s AND user_id = %s",
            (WS, VIEWER),
        )
    body = client.get(f"/v1/workspaces/{WS}/members").json()
    # Not listed as inactive: a picker offering someone who can no longer be named here is offering
    # an authorization the server will refuse.
    assert [item["userId"] for item in body["items"]] == [MAINTAINER]


# --------------------------------------------------------------------------------------------------
# The runner inventory
# --------------------------------------------------------------------------------------------------


def _enrolled_runner(db: str, *, status_value: str = "OFFLINE") -> str:
    runner_id = str(uuid.uuid4())
    with workspace_connection(db, WS) as conn:
        conn.execute(
            """
            INSERT INTO runner (id, workspace_id, name, status, session_key, platform, device_id,
                                interactive_session_id, console, profile_digest, profile)
            VALUES (%s, %s, 'desk-1', %s, %s, 'darwin', 'device-1', 'session-1', true, %s, %s)
            """,
            (
                runner_id,
                WS,
                status_value,
                "a" * 64,
                "b" * 64,
                '{"readerName": "VoiceOver", "readerVersion": "macOS 26.6", "browser": "Safari"}',
            ),
        )
    return runner_id


def test_the_inventory_reports_the_enrolled_profile_verbatim(client: TestClient, db: str) -> None:
    _signed_in(db, client)
    runner_id = _enrolled_runner(db)
    item = client.get(f"/v1/workspaces/{WS}/runners").json()["items"][0]
    assert item["runnerId"] == runner_id
    # A version difference is the whole reason a matched runner may still be the wrong one, so the
    # profile is reported as recorded rather than summarised into a status word.
    assert item["profile"]["readerName"] == "VoiceOver"
    assert item["profile"]["readerVersion"] == "macOS 26.6"


def test_a_runner_that_never_passed_a_preflight_reports_null_not_false(
    client: TestClient, db: str
) -> None:
    _signed_in(db, client)
    _enrolled_runner(db)
    item = client.get(f"/v1/workspaces/{WS}/runners").json()["items"][0]
    # "Never proved" and "proved a while ago" are different states, and an operator acts on them
    # differently. `false` would collapse them.
    assert item["preflightPassedAt"] is None
    assert item["status"] == "OFFLINE"


def test_a_runner_row_existing_is_not_reported_as_readiness(client: TestClient, db: str) -> None:
    """INV-02 at the read boundary.

    A runner process being reachable is not proof that a screen reader is running on it. The
    response says so in words, because the place that inference gets made is a UI reading a list.
    """
    _signed_in(db, client)
    _enrolled_runner(db)
    body = client.get(f"/v1/workspaces/{WS}/runners").json()
    assert "not inferred from the runner process being reachable" in body["readinessMeaning"]
    assert "no status here is evidence that a journey will pass" in body["readinessMeaning"]


def test_the_inventory_does_not_cross_workspaces(client: TestClient, db: str) -> None:
    _signed_in(db, client)
    _enrolled_runner(db)
    with workspace_connection(db, WS_OTHER) as conn:
        conn.execute(
            """
            INSERT INTO runner (id, workspace_id, name, status, session_key, platform, device_id,
                                interactive_session_id, console, profile_digest, profile)
            VALUES (%s, %s, 'theirs', 'READY', %s, 'darwin', 'device-2', 'session-2', true, %s,
                    '{}')
            """,
            (str(uuid.uuid4()), WS_OTHER, "c" * 64, "d" * 64),
        )
    items = client.get(f"/v1/workspaces/{WS}/runners").json()["items"]
    assert [i["name"] for i in items] == ["desk-1"]
