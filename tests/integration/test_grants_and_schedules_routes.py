"""Standing grants and schedules through the real HTTP surface.

Module 19 built the domain for both and module 18 gave neither an HTTP route, so a standing
authorization could be created only by writing SQL. That is the gap these close.

The test that matters most is `test_a_restored_grant_can_be_revalidated_through_the_api`. Module 27
introduced `revalidation_required` and no way to clear it, which made every restored grant
permanently unusable — a fail-closed control with no door, and the failure was silent: schedules
simply stopped admitting occurrences. The route and this test are the door.

Requirements: FR-015, FR-017, FR-021. Invariants: INV-07, INV-12.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE
from accessforge_api.config import ApiSettings
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    connect,
    migrate,
    restore,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import grants as grant_store
from accessforge_persistence import projects as project_store

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x310))
OWNER = str(uuid.UUID(int=0x311))
VIEWER = str(uuid.UUID(int=0x312))
DIGEST = "a" * 64


def _tomorrow(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


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
        conn.execute("DELETE FROM global_audit_event")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Grants')", (WS,))
        for user in (OWNER, VIEWER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
            )
    with workspace_connection(test_database_url, WS) as conn:
        for user, role in ((OWNER, "OWNER"), (VIEWER, "VIEWER")):
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,%s)",
                (WS, user, role),
            )
        budgets.configure_entitlement(
            conn,
            workspace_id=WS,
            max_runs_per_day=100,
            max_actions_per_day=10_000,
            max_wall_seconds_per_day=86_400,
            max_model_tokens_per_day=1_000_000,
            max_concurrent_runs=10,
            configured_by="test-fixture",
            reason="generous, so these tests exercise the routes rather than the limit",
        )
    yield test_database_url


@pytest.fixture()
def client(db: str, settings: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture()
def owner(db: str, client: TestClient) -> tuple[str, str]:
    from accessforge_api.auth import issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=OWNER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return issued.session_token, issued.csrf_token


@pytest.fixture()
def project_id(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        return project_store.create_project(conn, workspace_id=WS, name="Grants project")


@pytest.fixture()
def journey_version_id(db: str, project_id: str) -> str:
    """A real frozen journey version.

    `schedule.journey_version_id` has a composite foreign key to `journey_version`, so a schedule
    naming an invented UUID fails at the database rather than at the authorization check this test
    is about. Composite `(id, workspace_id)`, because a foreign key check bypasses row-level
    security and a plain `id` reference would let a schedule point at another tenant's journey.
    """
    version_id = str(uuid.uuid4())
    with workspace_connection(db, WS) as conn:
        conn.execute(
            """
            INSERT INTO journey_version
                (id, workspace_id, project_id, name, platform, journey_digest,
                 assertion_set_digest, fixture_digest, navigator_policy_digest, navigator_policy,
                 reviewer_summary)
            VALUES (%s, %s, %s, 'j', 'darwin', %s, %s, %s, %s, '{}', '{}')
            """,
            (version_id, WS, project_id, DIGEST, DIGEST, DIGEST, DIGEST),
        )
    return version_id


def _sign_in_again(db: str, client: TestClient) -> str:
    """Reconciliation revokes every session, including this test's.

    That is the control working, not an inconvenience: a token minted before the snapshot may have
    been revoked after it, and the revocation is not in the restored data. Every test that
    reconciles has to sign in again afterwards, exactly as a person would.
    """
    from accessforge_api.auth import issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=OWNER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return issued.csrf_token


def _create_grant(
    client: TestClient, csrf: str, project_id: str, **overrides: object
) -> httpx2.Response:
    body = {
        "projectId": project_id,
        "environment": "staging",
        "allowedJourneyVersionIds": [str(uuid.uuid4())],
        "allowedPolicyVersionIds": [str(uuid.uuid4())],
        "permittedEffects": [],
        "actionBudget": 100,
        "wallTimeBudgetSeconds": 600,
        "expiresAt": _tomorrow(),
        **overrides,
    }
    return client.post(
        f"/v1/workspaces/{WS}/execution-grants", json=body, headers={CSRF_HEADER: csrf}
    )


# --- creating a grant ----------------------------------------------------------------------------


def test_a_grant_is_created_bounded_and_readable(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    _, csrf = owner
    response = _create_grant(client, csrf, project_id)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["revision"] == 1
    assert body["revoked"] is False
    assert body["usable"] is True
    assert body["revalidationRequired"] is False
    assert "never authorizes a patch or a publication" in body["authorizes"]
    assert response.headers["ETag"] == '"1"'

    read = client.get(f"/v1/workspaces/{WS}/execution-grants/{body['grantId']}")
    assert read.status_code == 200
    assert read.json()["grantId"] == body["grantId"]


def test_a_viewer_cannot_mint_a_standing_grant(
    db: str, client: TestClient, project_id: str
) -> None:
    """RUN_APPROVE, not RUN_REQUEST.

    A grant is a machine that requests runs unattended. Somebody trusted to request one run has not
    been trusted to build one of those, and collapsing the two would make the approval boundary
    decorative.
    """
    from accessforge_api.auth import issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=VIEWER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)

    response = _create_grant(client, issued.csrf_token, project_id)
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("actionBudget", 0, "between 1"),
        ("actionBudget", 10_001, "between 1"),
        ("wallTimeBudgetSeconds", 0, "between 1"),
        ("allowedJourneyVersionIds", [], "at least one journey version"),
        ("expiresAt", "2020-01-01T00:00:00Z", "in the past"),
    ],
)
def test_an_unbounded_or_nonsensical_grant_is_refused(
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    field: str,
    value: object,
    fragment: str,
) -> None:
    """Every bound is required and every bound is finite.

    There is no NULL meaning unlimited anywhere in a grant, because a grant acts while nobody is
    watching: an unbounded action count is an open-ended licence to drive somebody's desktop.
    """
    _, csrf = owner
    response = _create_grant(client, csrf, project_id, **{field: value})
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_a_grant_lasting_longer_than_a_year_is_refused(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    _, csrf = owner
    response = _create_grant(client, csrf, project_id, expiresAt=_tomorrow(days=400))
    assert response.status_code == 400
    assert "nobody revisits" in response.json()["detail"]


# --- revocation ----------------------------------------------------------------------------------


def test_revoking_is_a_revision_not_a_delete_and_is_idempotent(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()

    first = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revoke",
        headers={CSRF_HEADER: csrf, "If-Match": "1"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revoked"] is True
    assert first.json()["revokedAt"] is not None
    assert first.json()["usable"] is False

    # Same call again, at the new revision. The operator most likely to revoke twice is the one who
    # is not sure the first attempt landed.
    revision = first.json()["revision"]
    second = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revoke",
        headers={CSRF_HEADER: csrf, "If-Match": str(revision)},
    )
    assert second.status_code == 200
    assert second.json()["revision"] == revision

    # And it is still listed. "What was allowed to run, and when did that stop" needs both halves.
    listed = client.get(f"/v1/workspaces/{WS}/execution-grants").json()["items"]
    assert [g["grantId"] for g in listed] == [grant["grantId"]]


def test_revoking_without_if_match_is_refused(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    response = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revoke",
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 428
    assert response.json()["code"] == "IF_MATCH_REQUIRED"


def test_revoking_at_a_stale_revision_is_refused(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    response = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revoke",
        headers={CSRF_HEADER: csrf, "If-Match": "99"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


# --- revalidation after a restore ----------------------------------------------------------------


def test_a_restored_grant_can_be_revalidated_through_the_api(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    backup_database_url: str,
) -> None:
    """The door module 27 did not build.

    Reconciliation marks every restored grant as requiring revalidation, and until this route
    existed nothing could clear it: the grant was unusable forever and the symptom was a schedule
    that quietly stopped firing. This drives the whole loop — create, reconcile, observe unusable,
    revalidate, observe usable — through the real HTTP surface.
    """
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    assert grant["revalidationRequired"] is False

    # Reconciliation runs with an elevated role, exactly as it does after a real restore: an
    # unscoped *application* connection sees zero rows of every tenant table, so every UPDATE
    # would report success and change nothing.
    with connect(backup_database_url) as conn:
        report = restore.reconcile(conn, operator="test", restore_id="an-archive")
        conn.commit()
    assert grant["grantId"] in report.grants_requiring_revalidation

    # Reconciliation revoked every session, this test's included. Asserted rather than worked
    # around: after a restore, everybody signs in again, and a test that silently kept working
    # would mean the sessions had survived.
    assert client.get(f"/v1/workspaces/{WS}/execution-grants").status_code == 401
    csrf = _sign_in_again(db, client)

    unusable = client.get(f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}").json()
    assert unusable["revalidationRequired"] is True
    assert unusable["usable"] is False
    assert "restored from a backup" in unusable["unusableBecause"]

    confirmed = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revalidations",
        headers={CSRF_HEADER: csrf, "If-Match": str(unusable["revision"])},
    )
    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    assert body["revalidationRequired"] is False
    assert body["usable"] is True
    # A person, by name. "Somebody confirmed this" with nobody attached is not a confirmation.
    assert body["revalidatedBy"] == OWNER
    assert body["revalidatedAt"] is not None
    assert "does not make an interrupted run complete" in body["confirmed"]


def test_revalidating_a_grant_that_was_never_restored_is_refused(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    """Accepting it would record somebody vouching for something nobody had questioned."""
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    response = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revalidations",
        headers={CSRF_HEADER: csrf, "If-Match": "1"},
    )
    assert response.status_code == 400
    assert "does not require revalidation" in response.json()["detail"]


def test_revalidation_does_not_resurrect_a_revoked_grant(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    backup_database_url: str,
) -> None:
    """Otherwise a restore becomes a way to undo a revocation."""
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    revoked = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revoke",
        headers={CSRF_HEADER: csrf, "If-Match": "1"},
    ).json()

    with connect(backup_database_url) as conn:
        conn.execute(
            "UPDATE execution_grant SET revalidation_required = true WHERE id = %s",
            (grant["grantId"],),
        )
        conn.commit()

    response = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revalidations",
        headers={CSRF_HEADER: csrf, "If-Match": str(revoked["revision"])},
    )
    assert response.status_code == 400
    assert "does not bring a withdrawn one back" in response.json()["detail"]


def test_revalidating_at_a_stale_revision_is_refused(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    backup_database_url: str,
) -> None:
    """A grant widened between reading it and confirming it is one nobody actually approved."""
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    with connect(backup_database_url) as conn:
        restore.reconcile(conn, operator="test", restore_id="an-archive")
        conn.commit()
    csrf = _sign_in_again(db, client)

    # Revision 1 is what the caller read *before* the restore. Reconciliation moves it, so this is
    # somebody confirming an authorization they have not looked at since it changed -- which is the
    # whole thing a revalidation is supposed to be.
    response = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revalidations",
        headers={CSRF_HEADER: csrf, "If-Match": "1"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


# --- schedules -----------------------------------------------------------------------------------


def test_a_schedule_is_created_against_a_grant_and_read_back(
    client: TestClient, owner: tuple[str, str], project_id: str, journey_version_id: str
) -> None:
    _, csrf = owner
    journey = journey_version_id
    grant = _create_grant(client, csrf, project_id, allowedJourneyVersionIds=[journey]).json()

    response = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "Europe/London",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["grantRevisionAtApproval"] == 1
    assert body["pausedAt"] is None
    assert "does not run anything" in body["meaning"]

    listed = client.get(f"/v1/workspaces/{WS}/schedules").json()["items"]
    assert [s["scheduleId"] for s in listed] == [body["scheduleId"]]


def test_a_schedule_cannot_name_a_journey_the_grant_does_not_cover(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    """A widening of scope dressed as configuration.

    Discovered here rather than at the first occurrence, which is after somebody believed it was
    approved.
    """
    _, csrf = owner
    grant = _create_grant(client, csrf, project_id).json()
    response = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "sneaky",
            "grantId": grant["grantId"],
            "journeyVersionId": str(uuid.uuid4()),
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "cannot broaden the authorization" in response.json()["detail"]


def test_a_schedule_cannot_be_created_against_an_unrevalidated_grant(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    journey_version_id: str,
    backup_database_url: str,
) -> None:
    """409, not 400: the body is fine and the state is not."""
    _, csrf = owner
    journey = journey_version_id
    grant = _create_grant(client, csrf, project_id, allowedJourneyVersionIds=[journey]).json()
    with connect(backup_database_url) as conn:
        restore.reconcile(conn, operator="test", restore_id="an-archive")
        conn.commit()
    csrf = _sign_in_again(db, client)

    response = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 409
    assert "restored from a backup" in response.json()["detail"]


def test_pausing_keeps_the_schedule_and_who_stopped_it(
    client: TestClient, owner: tuple[str, str], project_id: str, journey_version_id: str
) -> None:
    _, csrf = owner
    journey = journey_version_id
    grant = _create_grant(client, csrf, project_id, allowedJourneyVersionIds=[journey]).json()
    schedule = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    ).json()

    paused = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/pause",
        headers={CSRF_HEADER: csrf, "If-Match": str(schedule["revision"])},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["pausedAt"] is not None
    assert paused.json()["pausedBy"] == OWNER

    # Still listed. A paused schedule that vanished would look deleted.
    assert len(client.get(f"/v1/workspaces/{WS}/schedules").json()["items"]) == 1

    resumed = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/resume",
        headers={CSRF_HEADER: csrf, "If-Match": str(paused.json()["revision"])},
    )
    assert resumed.status_code == 200
    assert resumed.json()["pausedAt"] is None


def test_occurrences_report_a_skip_rather_than_an_absence(
    client: TestClient, owner: tuple[str, str], project_id: str, journey_version_id: str
) -> None:
    """A schedule silenced by a revoked grant must not look like one that never existed."""
    _, csrf = owner
    journey = journey_version_id
    grant = _create_grant(client, csrf, project_id, allowedJourneyVersionIds=[journey]).json()
    schedule = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    ).json()

    body = client.get(f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/occurrences").json()
    assert body["scheduleId"] == schedule["scheduleId"]
    assert body["items"] == []
    assert "indistinguishable from one that never existed" in body["meaning"]


def test_an_unknown_grant_id_is_not_found(client: TestClient, owner: tuple[str, str]) -> None:
    response = client.get(f"/v1/workspaces/{WS}/execution-grants/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_a_grant_that_exists_in_another_workspace_is_not_found(
    db: str, client: TestClient, owner: tuple[str, str]
) -> None:
    """The uniform 404, tested against a grant that genuinely exists.

    A random UUID only proves that an unknown id is a 404, which is the easy half. The half that
    matters is that a grant which *does* exist, in a workspace this caller cannot see, is
    indistinguishable from one that does not — otherwise the response is an oracle for what other
    tenants hold.
    """
    _, csrf = owner
    other_ws = str(uuid.UUID(int=0x319))
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Elsewhere')", (other_ws,))
    with workspace_connection(db, other_ws) as conn:
        other_project = project_store.create_project(
            conn, workspace_id=other_ws, name="Their project"
        )
        elsewhere = grant_store.create_grant(
            conn,
            workspace_id=other_ws,
            project_id=other_project,
            environment="staging",
            allowed_journey_version_ids=[str(uuid.uuid4())],
            allowed_policy_version_ids=["p1"],
            permitted_effects=[],
            action_budget=10,
            wall_time_budget_seconds=60,
            expires_at=_tomorrow(),
        )

    # It exists. Confirmed here so the assertion below is about visibility rather than existence.
    with workspace_connection(db, other_ws) as conn:
        assert grant_store.load_grant(conn, grant_id=elsewhere.grant.grant_id)

    response = client.get(f"/v1/workspaces/{WS}/execution-grants/{elsewhere.grant.grant_id}")
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert elsewhere.grant.grant_id not in response.text

    # And it is absent from the listing rather than shown as inaccessible.
    listed = client.get(f"/v1/workspaces/{WS}/execution-grants").json()["items"]
    assert elsewhere.grant.grant_id not in [g["grantId"] for g in listed]


def test_a_restore_stops_a_schedule_and_only_two_deliberate_confirmations_restart_it(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    journey_version_id: str,
    backup_database_url: str,
) -> None:
    """The whole loop, which was a dead end in three separate ways before this module.

    A restore stops every schedule, because reconciliation moves the grant's revision and a schedule
    is bound to the revision it was approved against. Getting back from that needs two separate
    decisions by a person, and the point of this test is that they are separate:

      1. **Revalidate the grant** — "this standing authorization is still valid."
      2. **Re-approve the schedule** — "this particular recurring job should start again."

    Making the second follow automatically from the first would restart work nobody asked to
    restart, possibly overnight, against a real desktop, on the strength of one click during an
    incident. So the schedule is still stopped after step 1, and this asserts it.
    """
    _, csrf = owner
    grant = _create_grant(
        client, csrf, project_id, allowedJourneyVersionIds=[journey_version_id]
    ).json()
    schedule = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey_version_id,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    ).json()
    assert schedule["grantRevisionAtApproval"] == 1

    with connect(backup_database_url) as conn:
        restore.reconcile(conn, operator="test", restore_id="an-archive")
        conn.commit()
    csrf = _sign_in_again(db, client)

    # Step 1: the grant. The schedule is still bound to revision 1 and the grant has moved twice.
    after_restore = client.get(f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}").json()
    assert after_restore["usable"] is False
    revalidated = client.post(
        f"/v1/workspaces/{WS}/execution-grants/{grant['grantId']}/revalidations",
        headers={CSRF_HEADER: csrf, "If-Match": str(after_restore["revision"])},
    ).json()
    assert revalidated["usable"] is True

    stopped = client.get(f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}").json()
    assert stopped["grantRevisionAtApproval"] == 1
    assert stopped["grantRevisionAtApproval"] != revalidated["revision"]
    assert stopped["reapprovedAt"] is None

    # Step 2: the schedule. A separate decision, by a named person.
    resumed = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/reapprove",
        headers={CSRF_HEADER: csrf, "If-Match": str(stopped["revision"])},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["grantRevisionAtApproval"] == revalidated["revision"]
    assert body["reapprovedBy"] == OWNER
    assert body["reapprovedAt"] is not None
    assert "not about capacity" in body["confirmed"]


def test_a_schedule_cannot_be_reapproved_while_its_grant_is_unusable(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    journey_version_id: str,
    backup_database_url: str,
) -> None:
    """Order matters, and the refusal says so.

    Re-approving a schedule cannot make its grant usable. An operator who reached for this first
    needs to be told to confirm the grant, not handed a schedule that will skip every occurrence.
    """
    _, csrf = owner
    grant = _create_grant(
        client, csrf, project_id, allowedJourneyVersionIds=[journey_version_id]
    ).json()
    schedule = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey_version_id,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    ).json()

    with connect(backup_database_url) as conn:
        restore.reconcile(conn, operator="test", restore_id="an-archive")
        conn.commit()
    csrf = _sign_in_again(db, client)

    response = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/reapprove",
        headers={CSRF_HEADER: csrf, "If-Match": str(schedule["revision"])},
    )
    assert response.status_code == 409
    assert "confirm the grant first" in response.json()["detail"]


# --- shapes that would otherwise be accepted quietly ---------------------------------------------


@pytest.mark.parametrize("field", ["allowedJourneyVersionIds", "allowedPolicyVersionIds"])
def test_a_bare_string_is_not_accepted_as_a_one_entry_scope(
    client: TestClient, owner: tuple[str, str], project_id: str, field: str
) -> None:
    """A string is iterable, so `[str(v) for v in value]` turns "abc" into three entries.

    Accepted, stored, and discovered only when a schedule matches none of them — a scope nobody
    meant, made of the letters of something somebody typed.
    """
    _, csrf = owner
    response = _create_grant(client, csrf, project_id, **{field: "not-a-list"})
    assert response.status_code == 400
    assert "must be an array" in response.json()["detail"]


def test_a_malformed_journey_identifier_is_refused_rather_than_reaching_the_database(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    """A malformed UUID at a UUID comparison is a driver error and a 500."""
    _, csrf = owner
    response = _create_grant(client, csrf, project_id, allowedJourneyVersionIds=["not-a-uuid"])
    assert response.status_code == 400
    assert "not a valid identifier" in response.json()["detail"]


def test_a_grant_of_365_days_and_23_hours_is_refused(
    client: TestClient, owner: tuple[str, str], project_id: str
) -> None:
    """`timedelta.days` truncates, so this reads as exactly 365 and slips past the ceiling.

    A bound that can be exceeded by rounding is not a bound.
    """
    _, csrf = owner
    expiry = (datetime.now(UTC) + timedelta(days=365, hours=23)).isoformat().replace("+00:00", "Z")
    response = _create_grant(client, csrf, project_id, expiresAt=expiry)
    assert response.status_code == 400
    assert "nobody revisits" in response.json()["detail"]


def test_a_concurrent_pause_is_not_silently_undone_by_a_reapproval(
    db: str,
    client: TestClient,
    owner: tuple[str, str],
    project_id: str,
    journey_version_id: str,
) -> None:
    """The check-then-use window between `If-Match` and the row lock.

    The route validated the revision and the persistence function acquired the row afterwards.
    A pause landing in that gap was accepted, unnoticed, and then reversed by the re-approval's
    `paused_at = NULL` — somebody's deliberate stop undone by a request that never saw it. The
    revision is now confirmed inside the transaction while the row is locked.

    Simulated by sending a stale revision, which is exactly the state the loser of that race holds.
    """
    _, csrf = owner
    grant = _create_grant(
        client, csrf, project_id, allowedJourneyVersionIds=[journey_version_id]
    ).json()
    schedule = client.post(
        f"/v1/workspaces/{WS}/schedules",
        json={
            "name": "nightly",
            "grantId": grant["grantId"],
            "journeyVersionId": journey_version_id,
            "sourceRef": "main",
            "cronExpression": "0 2 * * *",
            "timezone": "UTC",
            "expiresAt": grant["expiresAt"],
        },
        headers={CSRF_HEADER: csrf},
    ).json()

    paused = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/pause",
        headers={CSRF_HEADER: csrf, "If-Match": str(schedule["revision"])},
    ).json()
    assert paused["pausedAt"] is not None

    # A request holding the pre-pause revision, as the loser of the race would.
    losing = client.post(
        f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}/reapprove",
        headers={CSRF_HEADER: csrf, "If-Match": str(schedule["revision"])},
    )
    assert losing.status_code == 409
    assert losing.json()["code"] == "STALE_REVISION"

    still_paused = client.get(f"/v1/workspaces/{WS}/schedules/{schedule['scheduleId']}").json()
    assert still_paused["pausedAt"] is not None


def test_the_published_contract_declares_how_authentication_works(
    client: TestClient,
) -> None:
    """A generated contract that declared no security would read as an open API.

    Authority is resolved inside `build_context` from a cookie and a CSRF header, neither of which
    appears in a route signature — so FastAPI cannot infer it and the document has to say it.
    """
    schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["sessionCookie"]["in"] == "cookie"
    assert schemes["csrfHeader"]["in"] == "header"

    mutating = schema["paths"]["/v1/workspaces/{workspace_id}/execution-grants"]["post"]
    assert mutating["security"] == [{"sessionCookie": [], "csrfHeader": []}]

    # Signing in cannot require a session.
    assert schema["paths"]["/v1/sessions"]["post"]["security"] == []
    assert schema["paths"]["/health/live"]["get"]["security"] == []


def test_the_published_contract_describes_the_error_shape_that_actually_arrives(
    client: TestClient,
) -> None:
    """FastAPI documents a 422 this application never returns.

    `RequestValidationError` is caught and reshaped into an RFC7807 document with status 400. A
    client generated from the old contract would branch on a field that never arrives.
    """
    schema = client.get("/openapi.json").json()
    assert "HTTPValidationError" not in schema["components"].get("schemas", {})

    operation = schema["paths"]["/v1/workspaces/{workspace_id}/execution-grants"]["post"]
    assert "422" not in operation["responses"]
    assert "400" in operation["responses"]
    assert "application/problem+json" in operation["responses"]["400"]["content"]

    problem = schema["components"]["schemas"]["ProblemDetail"]
    assert "RESOURCE_NOT_FOUND" in problem["properties"]["code"]["enum"]

    # And the real response matches the documented shape.
    body = client.get(f"/v1/workspaces/{WS}/execution-grants/{uuid.uuid4()}").json()
    assert set(problem["required"]) <= set(body)
