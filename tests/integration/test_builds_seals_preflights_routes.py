"""Builds, seals and preflights through the real HTTP surface.

These three routes close the last gap in module 18 whose domain already existed — and one of them
closes a hole the others revealed. `POST /runs` now refuses a manifest digest nothing sealed, and
until `POST /projects/{id}/seals` existed a manifest could only be produced by writing SQL: the
product's central identity step had no surface at all, which is precisely why nobody noticed the
run route was taking the digest on trust.

What each route refuses is the interesting part:

* a build records a dirty tree rather than refusing it, and nothing downstream may call it clean;
* a seal covers every input or none, because a manifest missing one field describes an identity that
  does not cover it;
* a preflight's success is computed by the server from the submitted checks, never taken from the
  submission, and an absent check is not a passing check.

Requirements: FR-002, FR-004, FR-005, FR-010, FR-014. Invariants: INV-02, INV-04.
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
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE
from accessforge_api.config import ApiSettings
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import (
    projects as project_store,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x350))
OWNER = str(uuid.UUID(int=0x351))
VIEWER = str(uuid.UUID(int=0x352))


def _digest(seed: str) -> str:
    return str(digest({"seed": seed}))


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
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Sealing')", (WS,))
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
def csrf(db: str, client: TestClient) -> str:
    from accessforge_api.auth import issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=OWNER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return issued.csrf_token


@pytest.fixture()
def project(db: str) -> str:
    with workspace_connection(db, WS) as conn:
        return project_store.create_project(conn, workspace_id=WS, name="Sealed project")


@pytest.fixture()
def environment(client: TestClient, csrf: str, project: str) -> str:
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/environments",
        json={
            "name": "staging",
            "allowedOrigins": ["https://app.example.test"],
            "fixtureResetStrategy": "RESET_ENDPOINT",
            "observerCredentialRef": "observer-profile",
            "resetCredentialRef": "reset-profile",
            "permittedEffects": ["FORM_SUBMIT"],
            "expiresAt": (datetime.now(UTC) + timedelta(days=30))
            .isoformat()
            .replace("+00:00", "Z"),
        },
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["environmentId"])


def _build_body(**overrides: Any) -> dict[str, Any]:
    return {
        "commitSha": "a" * 40,
        "treeDigest": _digest("tree"),
        "dirty": False,
        "dirtyPaths": [],
        "requestedRevision": "HEAD",
        "artifactDigest": _digest("artifact"),
        "identityObservable": True,
        **overrides,
    }


def _seal_body(build_id: str, environment_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "buildId": build_id,
        "environmentId": environment_id,
        "journeyDigest": _digest("journey"),
        "assertionSetDigest": _digest("assertions"),
        "fixtureDigest": _digest("fixture"),
        "runnerProfileDigest": _digest("profile"),
        "navigatorPolicyDigest": _digest("policy"),
        "evaluatorVersion": "1.0.0",
        "modelConfigDigest": _digest("model"),
        **overrides,
    }


# --- builds --------------------------------------------------------------------------------------


def test_a_build_records_the_source_and_the_artifact_together(
    client: TestClient, csrf: str, project: str
) -> None:
    """One operation, because they are one fact.

    An artifact digest with no source identity is untraceable; a source identity with no artifact is
    a commit nobody built. Recording them separately would let either half exist alone, and the half
    that goes missing is always the one a reader needed.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["buildId"]
    assert body["sourceSnapshotId"]
    assert body["dirty"] is False
    assert body["identityObservable"] is True


def test_a_dirty_tree_is_recorded_rather_than_refused(
    client: TestClient, csrf: str, project: str
) -> None:
    """Local development is a legitimate case. Calling it clean afterwards is not.

    The flag and the count of differing paths are stored and travel into every seal built on this
    build, so nothing downstream can describe a dirty tree as clean HEAD.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(dirty=True, dirtyPaths=["src/app.ts", "README.md"]),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dirty"] is True
    assert body["dirtyPathCount"] == 2
    assert "stays dirty in every seal" in body["meaning"]


def test_an_unobservable_artifact_identity_is_accepted_and_says_what_it_means(
    client: TestClient, csrf: str, project: str
) -> None:
    """`identityObservable: false` is a claim about the deployment, not about the build.

    The run may still execute; it simply cannot make a fully verified provenance claim. The
    alternative — refusing, or substituting a plausible digest — replaces a known limitation with an
    invented fact.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(identityObservable=False),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["identityObservable"] is False
    assert "cannot prove which artifact it serves" in response.json()["meaning"]


def test_a_build_missing_either_half_is_refused(
    client: TestClient, csrf: str, project: str
) -> None:
    for field in ("commitSha", "artifactDigest", "treeDigest", "requestedRevision"):
        body = _build_body()
        del body[field]
        response = client.post(
            f"/v1/workspaces/{WS}/projects/{project}/builds",
            json=body,
            headers={CSRF_HEADER: csrf},
        )
        assert response.status_code == 400, (field, response.text)
        assert field in response.json()["detail"]


def test_dirty_paths_must_be_an_array(client: TestClient, csrf: str, project: str) -> None:
    """A bare string is iterable and would be recorded as one path per character."""
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(dirty=True, dirtyPaths="src/app.ts"),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "must be an array" in response.json()["detail"]


def test_a_viewer_cannot_register_a_build(db: str, client: TestClient, project: str) -> None:
    from accessforge_api.auth import issue_session

    with workspace_connection(db, WS) as conn:
        issued = issue_session(conn, user_id=VIEWER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)

    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: issued.csrf_token},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_a_build_against_a_project_in_another_workspace_is_not_found(
    client: TestClient, csrf: str
) -> None:
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{uuid.uuid4()}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


# --- seals ---------------------------------------------------------------------------------------


def test_a_seal_produces_a_digest_a_run_can_be_requested_against(
    client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """The whole point of this route, end to end through the real API.

    Build, seal, request a run against the sealed digest. Before this existed the last step could
    only be reached by writing SQL, which is why nobody noticed `POST /runs` was taking the digest
    on trust.
    """
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()

    sealed = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(build["buildId"], environment),
        headers={CSRF_HEADER: csrf},
    )
    assert sealed.status_code == 201, sealed.text
    body = sealed.json()
    assert body["manifestDigest"] == body["requestRunWith"]
    assert "differ only by an approved patch" in body["meaning"]

    requested = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"projectId": project, "manifestDigest": body["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert requested.status_code == 202, requested.text

    # And it appears in the listing the UI reads to offer a real digest rather than invent one.
    listed = client.get(f"/v1/workspaces/{WS}/projects/{project}/manifests").json()
    assert body["manifestDigest"] in [m["manifestDigest"] for m in listed["items"]]


def test_a_run_cannot_be_requested_against_a_digest_nobody_sealed(
    client: TestClient, csrf: str, project: str
) -> None:
    """The hole this module closed.

    The field was previously taken on the caller's word: any 64-character hex string queued a run
    whose identity matched nothing, and the only guard was a UI screen refusing to offer one — a
    check in the one place a determined caller can skip. The failure then surfaced at dispatch, long
    after somebody believed the run was queued.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"projectId": project, "manifestDigest": _digest("never-sealed")},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_INPUT"
    assert "no sealed manifest has that digest" in response.json()["detail"]
    assert "POST /projects/{projectId}/seals" in response.json()["detail"]


def test_a_manifest_sealed_in_another_workspace_is_as_absent_as_one_never_sealed(
    db: str, client: TestClient, csrf: str, project: str, seal_manifest: Any
) -> None:
    """Row-level security does the work; this asserts the consequence.

    A digest is global-looking — 64 hex characters — so without scoping, one workspace could queue a
    run against another's sealed inputs and the run's identity would name a manifest its own tenant
    never authorized.
    """
    other_ws = str(uuid.UUID(int=0x359))
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Elsewhere')", (other_ws,))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'them@example.test')",
            (str(uuid.UUID(int=0x35A)),),
        )
    with workspace_connection(db, other_ws) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (other_ws, str(uuid.UUID(int=0x35A))),
        )
        their_project = project_store.create_project(
            conn, workspace_id=other_ws, name="Their project"
        )
    elsewhere = seal_manifest(
        db,
        workspace_id=other_ws,
        project_id=their_project,
        authorized_by=str(uuid.UUID(int=0x35A)),
    )

    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"projectId": project, "manifestDigest": elsewhere},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "no sealed manifest has that digest" in response.json()["detail"]


def test_a_seal_requires_every_input(
    client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """A manifest missing one field describes an identity that does not cover it.

    The gap would surface as two runs looking identical while differing in whatever nobody sealed.
    """
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()

    for field in ("journeyDigest", "runnerProfileDigest", "evaluatorVersion", "modelConfigDigest"):
        body = _seal_body(build["buildId"], environment)
        del body[field]
        response = client.post(
            f"/v1/workspaces/{WS}/projects/{project}/seals",
            json=body,
            headers={CSRF_HEADER: csrf},
        )
        assert response.status_code == 400, (field, response.text)
        assert field in response.json()["detail"]


def test_two_seals_over_identical_inputs_share_a_digest(
    client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """Deliberately not unique (migration 0006).

    Two runs with identical inputs share a manifest digest, and that is how a baseline and a
    candidate are shown to differ only by an approved patch. A unique constraint here would
    have made the product's central verification step impossible.
    """
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()
    body = _seal_body(build["buildId"], environment)

    first = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals", json=body, headers={CSRF_HEADER: csrf}
    ).json()
    second = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals", json=body, headers={CSRF_HEADER: csrf}
    ).json()

    assert first["manifestDigest"] == second["manifestDigest"]
    # Different rows, same digest. The seal is a record of an act; the digest is a property of the
    # inputs.
    assert first["sealedManifestId"] != second["sealedManifestId"]


def test_changing_one_input_changes_the_digest(
    client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """If it did not, two different runs would be indistinguishable by identity."""
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()

    base = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(build["buildId"], environment),
        headers={CSRF_HEADER: csrf},
    ).json()
    changed = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(build["buildId"], environment, evaluatorVersion="1.0.1"),
        headers={CSRF_HEADER: csrf},
    ).json()

    assert base["manifestDigest"] != changed["manifestDigest"]


def test_a_seal_against_an_unknown_build_is_not_found(
    client: TestClient, csrf: str, project: str, environment: str
) -> None:
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(str(uuid.uuid4()), environment),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    ("column", "fragment"),
    [
        ("revoked_at", "revoked"),
        ("expires_at", "expired"),
    ],
)
def test_a_seal_against_an_unusable_environment_is_refused(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    environment: str,
    column: str,
    fragment: str,
) -> None:
    """Sealing against an environment nobody currently authorizes.

    A manifest sealed against a revoked or expired environment would look authoritative and was
    never authorized, and the run it identified would be one nobody approved at the moment it ran.

    The environment is made unusable in the database rather than through a route, because the API
    refuses to *register* an expiry in the past -- correct, and it means this state is only
    reachable by time passing. An earlier version of this test re-registered the same environment
    name expecting that to supersede the first. It does not, so the test asserted a refusal and got
    a 201.
    """
    with workspace_connection(db, WS) as conn:
        conn.execute(
            f"UPDATE environment_manifest SET {column} = now() - interval '1 day' "  # noqa: S608
            "WHERE id = %s",
            (environment,),
        )

    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()

    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(build["buildId"], environment),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "CONFLICT"
    assert fragment in response.json()["detail"]


# --- preflights ----------------------------------------------------------------------------------


PROFILE = {
    "platform": "darwin",
    "readerName": "VoiceOver",
    "readerVersion": "14.0",
    "browserName": "Safari",
    "browserVersion": "26.0",
    "locale": "en-GB",
    "keyboardLayout": "ABC",
}

SESSION = {
    "deviceId": "device-1",
    "platform": "darwin",
    "interactiveSessionId": "session-1",
    "console": True,
}


@pytest.fixture()
def runner(client: TestClient, csrf: str) -> dict[str, Any]:
    """An enrolled runner. PREFLIGHT_REQUIRED, always — there is no body that enrols one READY."""
    token = client.post(
        f"/v1/workspaces/{WS}/runners/enrollment-tokens",
        json={"ttlSeconds": 600},
        headers={CSRF_HEADER: csrf},
    )
    assert token.status_code == 201, token.text

    enrolled = client.post(
        f"/v1/workspaces/{WS}/runners",
        json={
            "token": token.json()["token"],
            "name": "desk-1",
            "session": SESSION,
            "profile": PROFILE,
        },
        headers={CSRF_HEADER: csrf},
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["status"] == "PREFLIGHT_REQUIRED"
    return dict(enrolled.json())


def _preflight_body(runner: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS

    return {
        "runnerProfileDigest": runner["profileDigest"],
        "environmentConfigDigest": _digest("env"),
        "manifestDigest": _digest("manifest"),
        "observedReaderVersion": PROFILE["readerVersion"],
        "observedBrowserVersion": PROFILE["browserVersion"],
        "observedLocale": PROFILE["locale"],
        "observedKeyboardLayout": PROFILE["keyboardLayout"],
        "desktopSessionKey": "d" * 64,
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "checks": {str(check): "TRUE" for check in REQUIRED_PREFLIGHT_CHECKS},
        **overrides,
    }


def test_a_complete_preflight_makes_a_runner_ready(
    client: TestClient, csrf: str, runner: dict[str, Any]
) -> None:
    """READY means a server checked, not that a runner said so.

    `successful` is computed from the submitted checks and the server's own comparison of the
    observed reader and browser versions against the enrolled profile.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["successful"] is True
    assert body["runnerStatus"] == "READY"
    assert "not that the runner reported itself ready" in body["meaning"]


def test_a_runner_cannot_declare_itself_successful(
    client: TestClient, csrf: str, runner: dict[str, Any]
) -> None:
    """There is no `successful` field to send.

    A runner that could declare its own readiness would be the only witness to it, and the entire
    purpose of a preflight is that READY is a statement the server is willing to make.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, successful=True),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNEXPECTED_FIELD"


def test_an_absent_check_is_not_a_passing_check(
    client: TestClient, csrf: str, runner: dict[str, Any]
) -> None:
    """Coverage is compared for equality, so a check that stops being reported fails preflight.

    A supervisor that silently drops `NO_STALE_INPUT_SOURCE` after an upgrade would otherwise lose
    the guarantee while continuing to look ready — INV-02's shape applied to readiness: missing
    evidence never becomes a positive result.
    """
    from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS

    checks = {str(check): "TRUE" for check in REQUIRED_PREFLIGHT_CHECKS}
    dropped = sorted(checks)[0]
    del checks[dropped]

    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, checks=checks),
        headers={CSRF_HEADER: csrf},
    )
    # 201: the submission was recorded. What it established is in the body, and it established
    # nothing. Answering 400 would conflate a malformed request with a desktop that is not ready.
    assert response.status_code == 201, response.text
    assert response.json()["successful"] is False
    assert response.json()["runnerStatus"] != "READY"


def test_unknown_is_not_a_pass(client: TestClient, csrf: str, runner: dict[str, Any]) -> None:
    """A runner that could not determine whether the screen was locked has not shown it unlocked."""
    from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS

    checks = {str(check): "TRUE" for check in REQUIRED_PREFLIGHT_CHECKS}
    checks[sorted(checks)[0]] = "UNKNOWN"

    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, checks=checks),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["successful"] is False


def test_a_drifted_reader_version_is_caught_by_the_server_not_the_runner(
    client: TestClient, csrf: str, runner: dict[str, Any]
) -> None:
    """The runner reports every check TRUE, including that its version matches its profile.

    It does not. A supervisor that lied about its reader version would also lie about whether that
    version matches, so the server holds the enrolled profile and compares the observed values
    itself.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, observedReaderVersion="15.0"),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["successful"] is False
    assert body["runnerStatus"] != "READY"
    assert body["refusalSummary"]


def test_an_invented_check_name_is_refused(
    client: TestClient, csrf: str, runner: dict[str, Any]
) -> None:
    """Both vocabularies are closed.

    If a runner could name its own checks, "READER_SPEECH_CAPTURED" and "SPEECH_OK" would both
    appear over time and the required-coverage comparison would silently stop covering anything.
    """
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, checks={"EVERYTHING_IS_FINE": "TRUE"}),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "closed" in response.json()["detail"]


def test_checks_must_be_an_object(client: TestClient, csrf: str, runner: dict[str, Any]) -> None:
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner, checks=["READER_ACTIVE"]),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 400
    assert "must be an object" in response.json()["detail"]


def test_a_preflight_for_an_unknown_runner_is_not_found(client: TestClient, csrf: str) -> None:
    response = client.post(
        f"/v1/workspaces/{WS}/runners/{uuid.uuid4()}/preflights",
        json=_preflight_body({"profileDigest": _digest("profile")}),
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
