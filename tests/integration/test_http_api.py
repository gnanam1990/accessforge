"""The `/v1` surface against a real application and a real database.

Through FastAPI's TestClient, which runs the actual ASGI app: the route handlers, the dependency
graph, the exception handlers and the real connections. Not a mocked server — the module prompt is
explicit that missing upstream runtime proof stays BLOCKED rather than being substituted by one.

The negative cases it lists: unauthenticated access, a forged workspace in the body, a stale
revision, a changed idempotent body, revoked membership on replay, pagination crossing workspaces,
an unknown field, a malformed digest, 202 without completion, and unavailable storage.

Requirements: FR-001, FR-014, FR-016, FR-020. Invariants: INV-07, INV-08, INV-11, INV-12.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import issue_session
from accessforge_api.config import ApiSettings
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xF0))
WS_OTHER = str(uuid.UUID(int=0xF1))
OWNER = str(uuid.UUID(int=0xF2))
VIEWER = str(uuid.UUID(int=0xF3))
OUTSIDER = str(uuid.UUID(int=0xF4))


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
        for user in (OWNER, VIEWER, OUTSIDER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)",
                (user, f"{user}@example.test"),
            )
    with workspace_connection(test_database_url, WS) as conn:
        for user, role in ((OWNER, "OWNER"), (VIEWER, "VIEWER")):
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, %s)",
                (WS, user, role),
            )
    with workspace_connection(test_database_url, WS_OTHER) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'OWNER')",
            (WS_OTHER, OUTSIDER),
        )
    # Both workspaces need an entitlement, because requesting a run is now charged against one.
    # A workspace with none is refused rather than treated as unlimited: "nobody has decided what
    # your allowance is" and "you may do anything" are different states, and the second is not a
    # safe default for a system that drives a desktop and spends money on a model.
    for workspace in (WS, WS_OTHER):
        with workspace_connection(test_database_url, workspace) as conn:
            budgets.configure_entitlement(
                conn,
                workspace_id=workspace,
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
def client(db: str, settings: ApiSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _sign_in(db: str, user_id: str, workspace_id: str = WS) -> tuple[str, str]:
    """A real session, issued by module 03's own code path rather than forged here."""
    with workspace_connection(db, workspace_id) as conn:
        issued = issue_session(conn, user_id=user_id)
    return issued.session_token, issued.csrf_token


def _headers(csrf: str, **extra: str) -> dict[str, str]:
    from accessforge_api.auth import CSRF_HEADER

    return {CSRF_HEADER: csrf, **extra}


def _cookies(token: str) -> dict[str, str]:
    from accessforge_api.auth import SESSION_COOKIE

    return {SESSION_COOKIE: token}


# --- authentication and authority ----------------------------------------------------------------


def test_an_unauthenticated_request_is_refused(client: TestClient) -> None:
    response = client.get(f"/v1/workspaces/{WS}/projects")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_every_problem_carries_a_code_and_a_request_id(client: TestClient) -> None:
    """A caller branches on `code`; prose changes. `requestId` is the only id in the body."""
    body = client.get(f"/v1/workspaces/{WS}/projects").json()
    assert set(body) >= {"type", "title", "status", "code", "detail", "requestId"}
    uuid.UUID(body["requestId"])


def test_a_supplied_request_id_is_echoed(client: TestClient) -> None:
    body = client.get(
        f"/v1/workspaces/{WS}/projects", headers={"X-Request-Id": "correlation-1234"}
    ).json()
    assert body["requestId"] == "correlation-1234"


def test_an_authenticated_member_can_read(db: str, client: TestClient) -> None:
    """The control. Without it every refusal below could pass against an API that refuses all."""
    token, _ = _sign_in(db, OWNER)
    response = client.get(f"/v1/workspaces/{WS}/projects", cookies=_cookies(token))
    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None}


def test_a_member_of_another_workspace_gets_404_not_403(db: str, client: TestClient) -> None:
    """The cross-tenant existence oracle this is written to avoid.

    A 403 would confirm the workspace exists. The outsider holds a valid session and a real
    membership somewhere else, and learns nothing about workspace A.
    """
    token, _ = _sign_in(db, OUTSIDER, workspace_id=WS_OTHER)
    response = client.get(f"/v1/workspaces/{WS}/projects", cookies=_cookies(token))
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_a_viewer_cannot_create_a_project(db: str, client: TestClient) -> None:
    """403 here, because the caller *is* a member: the workspace's existence is not a secret."""
    token, csrf = _sign_in(db, VIEWER)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "p"},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_a_mutation_without_csrf_is_refused(db: str, client: TestClient) -> None:
    token, _ = _sign_in(db, OWNER)
    response = client.post(
        f"/v1/workspaces/{WS}/projects", json={"name": "p"}, cookies=_cookies(token)
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_REQUIRED"


def test_a_forged_workspace_in_the_body_is_refused(db: str, client: TestClient) -> None:
    """Authority comes from the session and the path. A mismatched body field grants nothing and is
    still refused: a client sending a different workspace is confused or probing, and silently using
    the safe value would hide both."""
    token, csrf = _sign_in(db, OWNER)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "p", "workspaceId": WS_OTHER},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 400
    assert response.json()["code"] in {"UNEXPECTED_FIELD", "INVALID_INPUT"}


def test_an_unexpected_field_is_refused_rather_than_ignored(db: str, client: TestClient) -> None:
    """An ignored field is one a later version might start reading."""
    token, csrf = _sign_in(db, OWNER)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "p", "isAdmin": True},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNEXPECTED_FIELD"


# --- projects ------------------------------------------------------------------------------------


def test_a_project_can_be_created_and_read_back(db: str, client: TestClient) -> None:
    token, csrf = _sign_in(db, OWNER)
    created = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "reference app"},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert created.status_code == 201
    project_id = created.json()["projectId"]

    fetched = client.get(f"/v1/workspaces/{WS}/projects/{project_id}", cookies=_cookies(token))
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "reference app"


def test_a_repository_without_an_authorizing_user_is_refused(db: str, client: TestClient) -> None:
    """Reachability is not consent, enforced by the domain and reported by the route."""
    token, csrf = _sign_in(db, OWNER)
    response = client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "p", "repositoryUrl": "https://example.test/repo.git"},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 400
    assert "consent" in response.json()["detail"]


def test_another_workspaces_project_is_not_visible(db: str, client: TestClient) -> None:
    """Pagination never crosses a tenant boundary, because the connection is workspace-scoped."""
    outsider_token, outsider_csrf = _sign_in(db, OUTSIDER, workspace_id=WS_OTHER)
    client.post(
        f"/v1/workspaces/{WS_OTHER}/projects",
        json={"name": "theirs"},
        cookies=_cookies(outsider_token),
        headers=_headers(outsider_csrf),
    )
    token, _ = _sign_in(db, OWNER)
    listing = client.get(f"/v1/workspaces/{WS}/projects", cookies=_cookies(token))
    assert listing.json()["items"] == []


# --- runs ----------------------------------------------------------------------------------------


def test_requesting_a_run_returns_202_and_no_outcome(
    db: str, client: TestClient, manifest: str
) -> None:
    """202, because requesting is not running, and the body carries no result.

    `NOT_EVALUATED` is what the run actually holds. A 201 with something outcome-shaped would invite
    a caller to read a status as a verdict.
    """
    token, csrf = _sign_in(db, OWNER)
    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["outcome"] == "NOT_EVALUATED"
    assert "Location" in response.headers


def test_a_run_reports_status_and_outcome_as_separate_fields(
    db: str, client: TestClient, manifest: str
) -> None:
    """Never merged. Status says how a run ended; outcome says what it established, and a run can
    end cleanly having established nothing."""
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]

    body = client.get(f"/v1/workspaces/{WS}/runs/{run_id}", cookies=_cookies(token)).json()
    assert body["status"] == "QUEUED"
    assert body["outcome"] == "NOT_EVALUATED"
    assert "state" not in body, "a single merged field is how the distinction gets lost"


def test_a_run_carries_an_etag_matching_its_revision(
    db: str, client: TestClient, manifest: str
) -> None:
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]
    response = client.get(f"/v1/workspaces/{WS}/runs/{run_id}", cookies=_cookies(token))
    assert response.headers["ETag"] == f'"{response.json()["revision"]}"'


def test_cancellation_returns_request_metadata_not_a_stopped_claim(
    db: str, client: TestClient, manifest: str
) -> None:
    """The distinction the module prompt insists on, asserted on the response body.

    `stopAcknowledged` is false and the run is still non-terminal. A `{"cancelled": true}` would be
    a lie told by a field name.
    """
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]
    revision = client.get(f"/v1/workspaces/{WS}/runs/{run_id}", cookies=_cookies(token)).json()[
        "revision"
    ]

    response = client.post(
        f"/v1/workspaces/{WS}/runs/{run_id}/cancel",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf, **{"If-Match": str(revision)}),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["stopAcknowledged"] is False
    assert body["cancellationRequestedAt"] is not None
    assert "nothing here" in body["meaning"]

    after = client.get(f"/v1/workspaces/{WS}/runs/{run_id}", cookies=_cookies(token)).json()
    assert after["status"] != "CANCELLED", "a request is not a terminal state"


def test_a_revisioned_mutation_without_if_match_is_refused(
    db: str, client: TestClient, manifest: str
) -> None:
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]
    response = client.post(
        f"/v1/workspaces/{WS}/runs/{run_id}/cancel",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 428
    assert response.json()["code"] == "IF_MATCH_REQUIRED"


def test_a_stale_if_match_is_a_conflict(db: str, client: TestClient, manifest: str) -> None:
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]
    response = client.post(
        f"/v1/workspaces/{WS}/runs/{run_id}/cancel",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf, **{"If-Match": "9999"}),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STALE_REVISION"


def test_a_non_numeric_if_match_is_refused(db: str, client: TestClient, manifest: str) -> None:
    """A weak or opaque validator cannot be compared against a revision, so accepting one would mean
    accepting it and ignoring it."""
    token, csrf = _sign_in(db, OWNER)
    run_id = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()["runId"]
    response = client.post(
        f"/v1/workspaces/{WS}/runs/{run_id}/cancel",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf, **{"If-Match": 'W/"opaque"'}),
    )
    assert response.status_code == 400


# --- idempotency ---------------------------------------------------------------------------------


def test_the_same_key_and_body_replays_the_first_operation(
    db: str, client: TestClient, manifest: str
) -> None:
    token, csrf = _sign_in(db, OWNER)
    headers = _headers(csrf, **{"Idempotency-Key": "retry-1"})
    body = {"manifestDigest": manifest}

    first = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=headers
    )
    second = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=headers
    )
    assert first.json()["runId"] == second.json()["runId"], "one run, not two"
    assert second.headers.get("Idempotent-Replay") == "true"


def test_the_same_key_with_a_changed_body_is_a_conflict(
    db: str, client: TestClient, manifest: str
) -> None:
    """Two different operations wearing one name. Replaying the first would silently discard the
    second."""
    token, csrf = _sign_in(db, OWNER)
    headers = _headers(csrf, **{"Idempotency-Key": "retry-2"})

    client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": manifest},
        cookies=_cookies(token),
        headers=headers,
    )
    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": digest({"m": "different"})},
        cookies=_cookies(token),
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_a_replay_rechecks_authorization(db: str, client: TestClient, manifest: str) -> None:
    """The subtlety that matters.

    A stored response returned without re-checking would be a cached authorization decision. The
    membership is revoked between the original call and the retry, and the retry is refused rather
    than replayed.
    """
    token, csrf = _sign_in(db, OWNER)
    headers = _headers(csrf, **{"Idempotency-Key": "retry-3"})
    body = {"manifestDigest": manifest}

    first = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=headers
    )
    assert first.status_code == 202

    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() "
            "WHERE workspace_id = %s AND user_id = %s",
            (WS, OWNER),
        )

    replay = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=headers
    )
    assert replay.status_code == 404, "a revoked member is not told the workspace exists"
    assert "runId" not in replay.json()


def test_without_a_key_each_call_is_its_own_operation(
    db: str, client: TestClient, manifest: str
) -> None:
    """Idempotency is a client's tool for making a retry safe, not a server requirement."""
    token, csrf = _sign_in(db, OWNER)
    body = {"manifestDigest": manifest}
    first = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=_headers(csrf)
    )
    second = client.post(
        f"/v1/workspaces/{WS}/runs", json=body, cookies=_cookies(token), headers=_headers(csrf)
    )
    assert first.json()["runId"] != second.json()["runId"]


# --- pagination ----------------------------------------------------------------------------------


def test_pagination_is_bounded_and_a_nonsense_size_is_refused(db: str, client: TestClient) -> None:
    """Refused rather than clamped: silently returning fewer items than asked for leaves a caller
    believing they hold the whole list."""
    token, _ = _sign_in(db, OWNER)
    assert (
        client.get(f"/v1/workspaces/{WS}/runs?limit=100000", cookies=_cookies(token)).status_code
        == 400
    )
    assert (
        client.get(f"/v1/workspaces/{WS}/runs?limit=0", cookies=_cookies(token)).status_code == 400
    )


def test_a_cursor_walks_the_whole_list_exactly_once(
    db: str, client: TestClient, manifest: str
) -> None:
    token, csrf = _sign_in(db, OWNER)
    created = set()
    for _ in range(5):
        created.add(
            client.post(
                f"/v1/workspaces/{WS}/runs",
                json={"manifestDigest": manifest},
                cookies=_cookies(token),
                headers=_headers(csrf),
            ).json()["runId"]
        )

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        url = f"/v1/workspaces/{WS}/runs?limit=2" + (f"&after={cursor}" if cursor else "")
        page = client.get(url, cookies=_cookies(token)).json()
        seen.extend(item["runId"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)), "keyset pagination never repeats a row"
    assert set(seen) == created


# --- runners -------------------------------------------------------------------------------------


def test_an_enrolled_runner_is_never_ready(db: str, client: TestClient) -> None:
    """No request body could produce a READY runner: readiness is a server conclusion from preflight
    evidence, and there is no preflight here."""
    token, csrf = _sign_in(db, OWNER)
    issued = client.post(
        f"/v1/workspaces/{WS}/runners/enrollment-tokens",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert issued.status_code == 201

    enrolled = client.post(
        f"/v1/workspaces/{WS}/runners",
        json={
            "token": issued.json()["token"],
            "name": "mac-01",
            "session": {
                "deviceId": "desk-01",
                "platform": "darwin",
                "interactiveSessionId": "100005",
                "console": True,
            },
            "profile": {
                "platform": "darwin",
                "readerName": "VoiceOver",
                "readerVersion": "10.0",
                "browserName": "Safari",
                "browserVersion": "18.2",
                "locale": "en-US",
                "keyboardLayout": "ANSI",
            },
        },
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert enrolled.status_code == 201
    assert enrolled.json()["status"] == "PREFLIGHT_REQUIRED"


def test_there_is_no_route_that_reads_an_enrollment_token_back(db: str, settings) -> None:
    """Structural, over the whole route table.

    An endpoint that could reveal an enrollment token would be a way to take over a tenant's
    desktop, and "only administrators can call it" is weaker than "it does not exist".
    """
    spec = create_app(settings).openapi()
    for path, methods in spec["paths"].items():
        if "enrollment-token" in path:
            assert set(methods) <= {"post"}, f"{path} exposes more than creation"


def test_a_process_identity_offered_as_a_desktop_is_refused(db: str, client: TestClient) -> None:
    """A container id in the session field type-checks perfectly and admits two attempts to one
    screen."""
    token, csrf = _sign_in(db, OWNER)
    issued = client.post(
        f"/v1/workspaces/{WS}/runners/enrollment-tokens",
        json={},
        cookies=_cookies(token),
        headers=_headers(csrf),
    ).json()
    response = client.post(
        f"/v1/workspaces/{WS}/runners",
        json={
            "token": issued["token"],
            "name": "mac-01",
            "session": {
                "deviceId": "desk-01",
                "platform": "linux",
                "interactiveSessionId": "1",
                "console": True,
            },
            "profile": {
                "platform": "linux",
                "readerName": "Orca",
                "readerVersion": "1",
                "browserName": "Firefox",
                "browserVersion": "1",
                "locale": "en-US",
                "keyboardLayout": "ANSI",
            },
        },
        cookies=_cookies(token),
        headers=_headers(csrf),
    )
    assert response.status_code == 400
    assert "supported interactive desktop" in response.json()["detail"]


# --- not found is uniform ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "runs/{id}",
        "projects/{id}",
        "findings/{id}",
        "journeys/{id}",
        "runners/{id}",
        "exports/{id}",
    ],
)
def test_an_unknown_resource_is_the_same_404_everywhere(
    db: str, client: TestClient, path: str
) -> None:
    token, _ = _sign_in(db, OWNER)
    url = f"/v1/workspaces/{WS}/" + path.replace("{id}", str(uuid.uuid4()))
    response = client.get(url, cookies=_cookies(token))
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    # No identifier and no hint about which of "does not exist" and "another tenant's" applies.
    assert "another workspace" in response.json()["detail"]


def test_no_problem_response_carries_a_stack_trace_or_a_secret(db: str, client: TestClient) -> None:
    """Every refusal goes through one handler, so there is no route that can answer with an
    exception's message."""
    token, csrf = _sign_in(db, OWNER)
    responses = [
        client.get(f"/v1/workspaces/{WS}/projects"),
        client.get(f"/v1/workspaces/{WS}/runs/{uuid.uuid4()}", cookies=_cookies(token)),
        client.post(
            f"/v1/workspaces/{WS}/projects",
            json={"name": "p", "unexpected": 1},
            cookies=_cookies(token),
            headers=_headers(csrf),
        ),
    ]
    for response in responses:
        text = response.text
        for leak in ("Traceback", 'File "', "psycopg", "postgresql://", csrf, token):
            assert leak not in text, f"{leak!r} appears in a problem response"
