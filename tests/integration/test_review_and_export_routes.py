"""Reading review requests and reviews, and the limits each record carries.

Module 24's screens rest on one distinction: **asking for a review, doing one, and what a review
authorizes are three different things.** These tests hold them apart at the read boundary, where a
UI is most likely to collapse them.

Requirements: FR-009, FR-011, FR-012, FR-013, FR-020. Invariants: INV-12, INV-15.
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
from accessforge_domain.authorization.roles import Role
from accessforge_domain.canonical import digest
from accessforge_domain.journeys import (
    ActionBudget,
    Assertion,
    AssertionKind,
    AssertionSet,
    FixtureBinding,
    JourneyDraft,
    TaskIntent,
    UnknownReason,
)
from accessforge_domain.states import ReviewVerdict
from accessforge_persistence import (
    assert_row_level_security_enforced,
    journeys,
    migrate,
    projects,
    reviews,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x240))
REVIEWER = str(uuid.UUID(int=0x241))
AUTHOR = str(uuid.UUID(int=0x242))
VIEWER = str(uuid.UUID(int=0x243))

PATCH = digest({"patch": "one"})
VERIFICATION = digest({"verification": "one"})
ENVIRONMENT = digest({"environment": "one"})


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


def _journey(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        project_id = projects.create_project(conn, workspace_id=WS, name="Reference app")
        draft = JourneyDraft(
            name="Recover from a form error",
            intent=TaskIntent(
                summary="Submit the contact form and recover from the error it reports",
                start_url="https://localhost:8443/contact",
                success_condition="The form reports it was received",
            ),
            assertions=AssertionSet(
                (
                    Assertion(
                        assertion_id="task-complete",
                        kind=AssertionKind.TASK_COMPLETION,
                        description="One submission was recorded for this fixture",
                        unknown_reasons=frozenset({UnknownReason.OBSERVER_UNREACHABLE}),
                    ),
                )
            ),
            fixture=FixtureBinding(template_id="contact-form", navigator_values={"name": "Rowan"}),
            budget=ActionBudget(max_actions=40, wall_time_seconds=300),
            platform="darwin",
            allowed_actions=frozenset({"NEXT", "ACTIVATE", "TYPE_TEXT"}),
            allowed_key_chords=frozenset(),
            allowed_effects=frozenset({"FIXTURE_SUBMIT"}),
        )
        compiled = journeys.freeze_version(
            conn, workspace_id=WS, project_id=project_id, draft=draft
        )
    return compiled.version.version_id


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Review')", (WS,))
        for user in (REVIEWER, AUTHOR, VIEWER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
            )
    with workspace_connection(test_database_url, WS) as conn:
        # The author holds REVIEWER too, so the self-review test exercises the independence policy
        # rather than the permission matrix: refusing them for lacking PATCH_REVIEW would prove
        # nothing about whether authorship is checked.
        for user, role in ((REVIEWER, "REVIEWER"), (AUTHOR, "REVIEWER"), (VIEWER, "VIEWER")):
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


def _sign_in(db: str, client: TestClient, user_id: str) -> dict[str, str]:
    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return {CSRF_HEADER: issued.csrf_token}


def _request(db: str, journey_version_id: str) -> str:
    with workspace_connection(db, WS) as conn:
        return reviews.request_review(
            conn,
            workspace_id=WS,
            patch_digest=PATCH,
            verification_digest=VERIFICATION,
            journey_version_id=journey_version_id,
            environment_digest=ENVIRONMENT,
            requested_by=AUTHOR,
            requested_of=REVIEWER,
        )


def _submitted(db: str, journey_version_id: str, request_id: str, **over: Any) -> str:
    with workspace_connection(db, WS) as conn:
        return reviews.submit_review(
            conn,
            workspace_id=WS,
            reviewer_id=over.pop("reviewer_id", REVIEWER),
            reviewer_role=Role.REVIEWER,
            patch_digest=PATCH,
            verification_digest=VERIFICATION,
            journey_version_id=journey_version_id,
            environment_digest=ENVIRONMENT,
            submission=reviews.ReviewSubmission(
                verdict=over.pop("verdict", ReviewVerdict.ACCEPT),
                observations=over.pop(
                    "observations", "The error is announced when focus reaches it"
                ),
                limitations=over.pop("limitations", "VoiceOver only; no other reader was tried"),
                used_assistive_technology=over.pop("used_at", True),
                assistive_technology_detail=over.pop("at_detail", "VoiceOver on macOS 26.6"),
            ),
            current_patch_digest=PATCH,
            current_verification_digest=VERIFICATION,
            patch_author_id=AUTHOR,
            request_id=request_id,
        )


# --------------------------------------------------------------------------------------------------
# Asking is not reviewing
# --------------------------------------------------------------------------------------------------


def test_a_request_with_no_review_reports_zero_rather_than_looking_answered(
    client: TestClient, db: str
) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    _sign_in(db, client, REVIEWER)

    body = client.get(f"/v1/workspaces/{WS}/review-requests").json()
    entry = next(item for item in body["items"] if item["reviewRequestId"] == request_id)
    # Treating an assignment as a review is how a process reports completed review that never
    # happened.
    assert entry["reviewCount"] == 0
    assert "nothing about whether they looked" in body["meaning"]


def test_a_request_reports_exactly_what_it_binds(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    _sign_in(db, client, REVIEWER)

    entry = next(
        item
        for item in client.get(f"/v1/workspaces/{WS}/review-requests").json()["items"]
        if item["reviewRequestId"] == request_id
    )
    assert entry["patchDigest"] == PATCH
    assert entry["verificationDigest"] == VERIFICATION
    assert entry["journeyVersionId"] == journey_version_id
    assert entry["environmentDigest"] == ENVIRONMENT


def test_the_count_moves_only_when_an_assessment_is_recorded(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    _submitted(db, journey_version_id, request_id)
    _sign_in(db, client, REVIEWER)

    entry = next(
        item
        for item in client.get(f"/v1/workspaces/{WS}/review-requests").json()["items"]
        if item["reviewRequestId"] == request_id
    )
    assert entry["reviewCount"] == 1


def test_the_queue_pages_rather_than_truncating(client: TestClient, db: str) -> None:
    """The shape a review found in module 22's environment listing, written again from memory.

    A first page with no cursor and no indication that it had stopped presents a prefix as the
    queue, and a request nobody can see is still waiting for somebody.
    """
    journey_version_id = _journey(db)
    for _ in range(3):
        _request(db, journey_version_id)
    _sign_in(db, client, REVIEWER)

    first = client.get(f"/v1/workspaces/{WS}/review-requests?limit=2").json()
    assert len(first["items"]) == 2
    assert first["nextCursor"] is not None

    second = client.get(
        f"/v1/workspaces/{WS}/review-requests?limit=2&after={first['nextCursor']}"
    ).json()
    assert len(second["items"]) == 1
    assert second["nextCursor"] is None
    ids = {item["reviewRequestId"] for item in first["items"] + second["items"]}
    assert len(ids) == 3


def test_a_malformed_queue_cursor_is_refused(client: TestClient, db: str) -> None:
    _journey(db)
    _sign_in(db, client, REVIEWER)
    assert client.get(f"/v1/workspaces/{WS}/review-requests?after=not-a-uuid").status_code == 400


def test_a_viewer_cannot_read_the_review_queue(client: TestClient, db: str) -> None:
    _journey(db)
    _sign_in(db, client, VIEWER)
    response = client.get(f"/v1/workspaces/{WS}/review-requests")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


# --------------------------------------------------------------------------------------------------
# What a review says, and what it does not
# --------------------------------------------------------------------------------------------------


def test_a_review_reports_what_it_was_bound_to(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    review_id = _submitted(db, journey_version_id, request_id)
    _sign_in(db, client, REVIEWER)

    body = client.get(f"/v1/workspaces/{WS}/reviews/{review_id}").json()
    assert body["boundTo"]["patchDigest"] == PATCH
    assert body["boundTo"]["verificationDigest"] == VERIFICATION
    assert body["verdict"] == "ACCEPT"


def test_a_review_carries_the_limits_of_what_it_establishes(client: TestClient, db: str) -> None:
    """Served rather than written into the interface.

    A client that composed its own list of limits would be free to shorten it, and the shortest
    version of this list is the one that makes an ACCEPT look like a release decision.
    """
    journey_version_id = _journey(db)
    review_id = _submitted(db, journey_version_id, _request(db, journey_version_id))
    _sign_in(db, client, REVIEWER)

    limits = client.get(f"/v1/workspaces/{WS}/reviews/{review_id}").json()["meansNothingAbout"]
    joined = " ".join(limits)
    assert "merge, deploy, publish or release" in joined
    assert "does not rewrite" in joined
    assert "people with disabilities in general" in joined


def test_assistive_technology_use_is_recorded_rather_than_assumed(
    client: TestClient, db: str
) -> None:
    journey_version_id = _journey(db)
    with_at = _submitted(db, journey_version_id, _request(db, journey_version_id))
    without_at = _submitted(
        db,
        journey_version_id,
        _request(db, journey_version_id),
        used_at=False,
        at_detail=None,
        verdict=ReviewVerdict.UNABLE_TO_ASSESS,
        limitations="the transcript was unavailable",
    )
    _sign_in(db, client, REVIEWER)

    # "A person accepted this" and "a person accepted this having driven it with a screen reader"
    # are very different claims, and the record keeps them apart.
    first = client.get(f"/v1/workspaces/{WS}/reviews/{with_at}").json()
    second = client.get(f"/v1/workspaces/{WS}/reviews/{without_at}").json()
    assert first["usedAssistiveTechnology"] is True
    assert first["assistiveTechnologyDetail"] == "VoiceOver on macOS 26.6"
    assert second["usedAssistiveTechnology"] is False
    assert second["assistiveTechnologyDetail"] is None


def test_a_correction_reads_as_two_records_rather_than_an_edit(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    first = _submitted(db, journey_version_id, request_id)
    with workspace_connection(db, WS) as conn:
        second = reviews.submit_review(
            conn,
            workspace_id=WS,
            reviewer_id=REVIEWER,
            reviewer_role=Role.REVIEWER,
            patch_digest=PATCH,
            verification_digest=VERIFICATION,
            journey_version_id=journey_version_id,
            environment_digest=ENVIRONMENT,
            submission=reviews.ReviewSubmission(
                verdict=ReviewVerdict.CHANGES_REQUESTED,
                observations="On a second reading the announcement is ambiguous",
                limitations="",
                used_assistive_technology=True,
                assistive_technology_detail="VoiceOver on macOS 26.6",
            ),
            current_patch_digest=PATCH,
            current_verification_digest=VERIFICATION,
            patch_author_id=AUTHOR,
            request_id=request_id,
            supersedes=first,
        )
    _sign_in(db, client, REVIEWER)

    original = client.get(f"/v1/workspaces/{WS}/reviews/{first}").json()
    correction = client.get(f"/v1/workspaces/{WS}/reviews/{second}").json()
    # Neither row is updated, so the history reads as what was said and then what was said instead.
    assert original["verdict"] == "ACCEPT"
    assert original["supersededBy"] == second
    assert correction["supersedes"] == first
    assert correction["supersededBy"] is None


def test_a_malformed_review_identifier_is_400_not_500(client: TestClient, db: str) -> None:
    _journey(db)
    _sign_in(db, client, REVIEWER)
    assert client.get(f"/v1/workspaces/{WS}/reviews/not-a-uuid").status_code == 400


def test_an_unknown_review_is_404(client: TestClient, db: str) -> None:
    _journey(db)
    _sign_in(db, client, REVIEWER)
    response = client.get(f"/v1/workspaces/{WS}/reviews/{uuid.uuid4()}")
    assert response.status_code == 404


# --------------------------------------------------------------------------------------------------
# Submitting one
# --------------------------------------------------------------------------------------------------


def _submission_body(journey_version_id: str, request_id: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "patchDigest": PATCH,
        "verificationDigest": VERIFICATION,
        "journeyVersionId": journey_version_id,
        "environmentDigest": ENVIRONMENT,
        "currentPatchDigest": PATCH,
        "currentVerificationDigest": VERIFICATION,
        "patchAuthorId": AUTHOR,
        "requestId": request_id,
        "verdict": "ACCEPT",
        "observations": "The error is announced when focus reaches it",
        "limitations": "VoiceOver only",
        "usedAssistiveTechnology": True,
        "assistiveTechnologyDetail": "VoiceOver on macOS 26.6",
    }
    body.update(over)
    return body


def test_a_review_of_content_that_has_since_changed_is_refused(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    headers = _sign_in(db, client, REVIEWER)

    response = client.post(
        f"/v1/workspaces/{WS}/reviews",
        json=_submission_body(
            journey_version_id, request_id, currentPatchDigest=digest({"patch": "two"})
        ),
        headers=headers,
    )
    assert response.status_code == 409
    # Carrying an acceptance forward onto changed bytes would attribute an opinion about other
    # content to the reviewer.
    assert "changed after the reviewer looked" in response.json()["detail"]


def test_the_patch_author_cannot_review_their_own_patch(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    headers = _sign_in(db, client, AUTHOR)

    response = client.post(
        f"/v1/workspaces/{WS}/reviews",
        json=_submission_body(journey_version_id, request_id, patchAuthorId=AUTHOR),
        headers=headers,
    )
    assert response.status_code == 403
    assert "self-review satisfies the process and not the purpose" in response.json()["detail"]


def test_assistive_technology_use_must_be_stated(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    headers = _sign_in(db, client, REVIEWER)
    body = _submission_body(journey_version_id, request_id)
    del body["usedAssistiveTechnology"]

    response = client.post(f"/v1/workspaces/{WS}/reviews", json=body, headers=headers)
    assert response.status_code == 400
    # Never inferred from a role, a checkbox elsewhere, or the absence of an answer.
    assert "Assistive-technology use is never inferred" in response.json()["detail"]


def test_unable_to_assess_must_say_what_was_missing(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    headers = _sign_in(db, client, REVIEWER)

    response = client.post(
        f"/v1/workspaces/{WS}/reviews",
        json=_submission_body(
            journey_version_id, request_id, verdict="UNABLE_TO_ASSESS", limitations=""
        ),
        headers=headers,
    )
    # The least useful record in the system is one that reports a person's time was spent and
    # nothing about why they could not answer.
    assert response.status_code in (400, 409)


def test_a_viewer_cannot_submit_a_review(client: TestClient, db: str) -> None:
    journey_version_id = _journey(db)
    request_id = _request(db, journey_version_id)
    headers = _sign_in(db, client, VIEWER)

    response = client.post(
        f"/v1/workspaces/{WS}/reviews",
        json=_submission_body(journey_version_id, request_id),
        headers=headers,
    )
    assert response.status_code == 403
