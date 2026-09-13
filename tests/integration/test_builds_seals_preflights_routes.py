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
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE
from accessforge_api.config import ApiSettings
from accessforge_contracts import validate
from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    execution_approvals,
    migrate,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import (
    projects as project_store,
)
from accessforge_persistence import (
    runners as runner_store,
)
from accessforge_persistence import (
    runs as run_store,
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


@pytest.fixture()
def execution_body(
    db: str, client: TestClient, csrf: str, project: str, environment: str
) -> dict[str, Any]:
    """HTTP-created source/environment and a synthetic frozen journey, not reader proof."""
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    )
    assert build.status_code == 201, build.text
    journey_id = str(uuid.uuid4())
    body = _seal_body(build.json()["buildId"], environment)
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO journey_version(id,workspace_id,project_id,name,platform,journey_digest,"
            "assertion_set_digest,fixture_digest,navigator_policy_digest,navigator_policy,"
            "reviewer_summary) VALUES (%s,%s,%s,'synthetic','web',%s,%s,%s,%s,'{}','{}')",
            (
                journey_id,
                WS,
                project,
                body["journeyDigest"],
                body["assertionSetDigest"],
                body["fixtureDigest"],
                body["navigatorPolicyDigest"],
            ),
        )
    body["execution"] = {
        "journeyVersionId": journey_id,
        "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "actionBudget": 10,
        "wallTimeBudgetSeconds": 30,
        "permittedEffects": ["FORM_SUBMIT"],
    }
    return body


def test_http_execution_seal_is_reviewable_idempotent_and_never_implicitly_approved(
    db: str, client: TestClient, csrf: str, project: str, execution_body: dict[str, Any]
) -> None:
    url = f"/v1/workspaces/{WS}/projects/{project}/seals"
    headers = {CSRF_HEADER: csrf, "Idempotency-Key": str(uuid.uuid4())}
    created = client.post(url, json=execution_body, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["manifestKind"] == "CANONICAL_EXECUTION"
    manifest = body["canonicalManifest"]
    validate("run-manifest.schema.json", manifest)
    assert digest(manifest) == body["manifestDigest"]
    for field, value in execution_body["execution"].items():
        assert manifest[field] == value
    assert manifest["workspaceId"] == WS and manifest["projectId"] == project
    assert client.post(url, json=execution_body, headers=headers).json() == body
    reviewed = client.get(f"{url}/{body['sealedManifestId']}")
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["canonicalManifest"] == manifest
    assert reviewed.headers["ETag"] == f'"{body["manifestDigest"]}"'
    listing = client.get(f"/v1/workspaces/{WS}/projects/{project}/manifests").json()
    assert listing["items"][0]["manifestKind"] == "CANONICAL_EXECUTION"
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM sealed_manifest").fetchone() == {"n": 1}
        other_project = project_store.create_project(conn, workspace_id=WS, name="Other")
    # A key scoped only to the route template used to return project A's seal through project B.
    assert (
        client.post(
            f"/v1/workspaces/{WS}/projects/{other_project}/seals",
            json=execution_body,
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/v1/workspaces/{WS}/projects/{other_project}/seals/{body['sealedManifestId']}"
        ).status_code
        == 404
    )
    again = client.post(url, json=execution_body, headers={CSRF_HEADER: csrf}).json()
    assert again["manifestDigest"] != body["manifestDigest"]
    for field in ("runId", "authorizationId"):
        assert again["canonicalManifest"][field] != manifest[field]
    admitted = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": body["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["runId"] == manifest["runId"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution", None),
        ("execution", {}),
        ("extra", True),
        ("actionBudget", True),
        ("actionBudget", 0),
        ("actionBudget", "10"),
        ("wallTimeBudgetSeconds", -1),
        ("journeyVersionId", 12),
        ("journeyVersionId", str(uuid.UUID(int=98765))),
        ("expiresAt", "not-a-date"),
        ("expiresAt", "2000-01-01T00:00:00Z"),
        ("expiresAt", "9999-01-01T00:00:00Z"),
        ("permittedEffects", "FORM_SUBMIT"),
        ("permittedEffects", [{}]),
        ("permittedEffects", ["FORM_SUBMIT", "FORM_SUBMIT"]),
        ("permittedEffects", ["DELETE_ALL"]),
    ],
)
def test_http_execution_seal_refuses_invalid_or_broadened_scope_atomically(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    execution_body: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    if field == "execution":
        execution_body[field] = value
    else:
        execution_body["execution"][field] = value
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 400, response.text
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sealed_manifest").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 0}


@pytest.mark.parametrize("changed", ["expiry", "environment"])
def test_canonical_admission_rechecks_live_scope_before_charging(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    environment: str,
    execution_body: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    created = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf},
    ).json()
    if changed == "environment":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE environment_manifest SET revoked_at=now() WHERE id=%s", (environment,)
            )
    else:
        future = datetime.now(UTC) + timedelta(hours=2)

        class FutureClock(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> FutureClock:
                return cls.fromtimestamp(future.timestamp(), tz=tz)

        monkeypatch.setattr(project_store, "datetime", FutureClock)
    response = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": created["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 409, response.text
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 0}
        assert conn.execute(
            "SELECT count(*) AS n FROM usage_event WHERE kind='RUN_ADMITTED'"
        ).fetchone() == {"n": 0}
    # Historical review remains possible, without claiming the scope is currently executable.
    reviewed = client.get(
        f"/v1/workspaces/{WS}/projects/{project}/seals/{created['sealedManifestId']}"
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["canonicalManifest"] == created["canonicalManifest"]


def test_execution_seal_read_and_write_permissions_and_legacy_review(
    db: str, client: TestClient, csrf: str, project: str, execution_body: dict[str, Any]
) -> None:
    from accessforge_api.auth import issue_session

    url = f"/v1/workspaces/{WS}/projects/{project}/seals"
    created = client.post(url, json=execution_body, headers={CSRF_HEADER: csrf}).json()
    legacy_body = {key: value for key, value in execution_body.items() if key != "execution"}
    legacy = client.post(url, json=legacy_body, headers={CSRF_HEADER: csrf}).json()
    assert legacy["manifestKind"] == "INPUT_FINGERPRINT"
    assert legacy["canonicalManifest"] is None
    assert client.get(f"{url}/{legacy['sealedManifestId']}").json()["canonicalManifest"] is None
    # A caller cannot choose the reserved identities or impersonate the authorizing actor.
    for field in ("runId", "authorizationId", "actorId"):
        refused = client.post(
            url, json={**execution_body, field: str(uuid.uuid4())}, headers={CSRF_HEADER: csrf}
        )
        assert refused.status_code == 400, refused.text
    assert client.post(url, json=execution_body).status_code == 403
    with workspace_connection(db, WS) as conn:
        session = issue_session(conn, user_id=VIEWER)
    client.cookies.set(SESSION_COOKIE, session.session_token)
    assert client.get(f"{url}/{created['sealedManifestId']}").status_code == 200
    assert (
        client.post(url, json=execution_body, headers={CSRF_HEADER: session.csrf_token}).status_code
        == 403
    )
    # Exact tenant and project pairing, including a principal belonging to both tenants.
    other_ws = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES (%s,'other')", (other_ws,))
    with workspace_connection(db, other_ws) as conn:
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,'OWNER')",
            (other_ws, VIEWER),
        )
        other_project = project_store.create_project(conn, workspace_id=other_ws, name="other")
    assert (
        client.get(
            f"/v1/workspaces/{other_ws}/projects/{other_project}/seals/{created['sealedManifestId']}"
        ).status_code
        == 404
    )
    client.cookies.clear()
    assert client.get(f"{url}/{created['sealedManifestId']}").status_code == 401


def test_empty_effects_are_preserved_without_broadening(
    client: TestClient, csrf: str, project: str, execution_body: dict[str, Any]
) -> None:
    execution_body["execution"]["permittedEffects"] = []
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["canonicalManifest"]["permittedEffects"] == []


def test_pre_upgrade_legacy_seal_replay_is_preserved_but_not_cross_project(
    db: str, client: TestClient, csrf: str, project: str, execution_body: dict[str, Any]
) -> None:
    from accessforge_persistence import idempotency

    url = f"/v1/workspaces/{WS}/projects/{project}/seals"
    legacy_body = {key: value for key, value in execution_body.items() if key != "execution"}
    created = client.post(url, json=legacy_body, headers={CSRF_HEADER: csrf}).json()
    # A real stored old-shape result under the exact committed pre-upgrade namespace.
    old_response = {
        key: value
        for key, value in created.items()
        if key not in {"canonicalManifest", "manifestKind"}
    }
    key = str(uuid.uuid4())
    with workspace_connection(db, WS) as conn:
        reserved = idempotency.reserve(
            conn,
            workspace_id=WS,
            principal_id=OWNER,
            route="POST /projects/seals",
            idempotency_key=key,
            request_digest=digest(legacy_body),
        )
        idempotency.complete(conn, operation_id=reserved.operation_id, result=old_response)
        other = project_store.create_project(conn, workspace_id=WS, name="Other")
    headers = {CSRF_HEADER: csrf, "Idempotency-Key": key}
    replay = client.post(url, json=legacy_body, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.json() == old_response
    assert (
        client.post(
            f"/v1/workspaces/{WS}/projects/{other}/seals",
            json=legacy_body,
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            url,
            json={**legacy_body, "evaluatorVersion": "changed"},
            headers=headers,
        ).status_code
        == 409
    )
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sealed_manifest").fetchone() == {"n": 1}


@pytest.mark.parametrize("completed", [False, True])
def test_incomplete_stored_seal_operation_refuses_without_reexecution(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    execution_body: dict[str, Any],
    completed: bool,
) -> None:
    from accessforge_persistence import idempotency

    key = str(uuid.uuid4())
    with workspace_connection(db, WS) as conn:
        reserved = idempotency.reserve(
            conn,
            workspace_id=WS,
            principal_id=OWNER,
            route="POST /projects/seals",
            idempotency_key=key,
            request_digest=digest(execution_body),
        )
        if completed:
            idempotency.complete(conn, operation_id=reserved.operation_id, result={})
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf, "Idempotency-Key": key},
    )
    assert response.status_code == 409, response.text
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sealed_manifest").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 0}


@pytest.fixture()
def manual_seal(
    client: TestClient, csrf: str, project: str, execution_body: dict[str, Any]
) -> dict[str, Any]:
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _approval_url(project: str, sealed: dict[str, Any]) -> str:
    return f"/v1/workspaces/{WS}/projects/{project}/seals/{sealed['sealedManifestId']}/approval"


def _approval_body(sealed: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifestDigest": sealed["manifestDigest"],
        "expiresAt": (datetime.now(UTC) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    }


def test_manual_approval_is_exact_independent_audited_and_revocable(
    db: str, client: TestClient, csrf: str, project: str, manual_seal: dict[str, Any]
) -> None:
    url = _approval_url(project, manual_seal)
    manifest = manual_seal["canonicalManifest"]
    assert client.get(url).status_code == 404
    assert client.get(url.removesuffix("/approval")).json()["revision"] == 0
    headers = {CSRF_HEADER: csrf, "If-Match": "0", "Idempotency-Key": str(uuid.uuid4())}
    body = _approval_body(manual_seal)
    with workspace_connection(db, WS) as conn:
        with pytest.raises(execution_approvals.Refused, match="no approval"):
            execution_approvals.assert_authorized(
                conn,
                sealed_manifest_id=manual_seal["sealedManifestId"],
                run_id=manifest["runId"],
                workspace_id=WS,
            )
    issued = client.post(url, json=body, headers=headers)
    assert issued.status_code == 201, issued.text
    decision = issued.json()
    assert decision["approvalId"] == manifest["authorizationId"]
    assert decision["targetId"] == manual_seal["sealedManifestId"]
    assert decision["targetDigest"] == manual_seal["manifestDigest"]
    assert decision["scope"] == "RUN_EFFECTS" and decision["actorId"] == OWNER
    assert decision["expectedRevision"] == 0 and decision["revokedAt"] is None
    assert client.post(url, json=body, headers=headers).json() == decision
    assert client.post(url, json=body, headers={**headers, "If-Match": "1"}).status_code == 409
    with workspace_connection(db, WS) as conn:
        assert (
            execution_approvals.assert_authorized(
                conn,
                sealed_manifest_id=manual_seal["sealedManifestId"],
                run_id=manifest["runId"],
                workspace_id=WS,
            )
            == manifest
        )
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 0}
        assert conn.execute(
            "SELECT actor_user,action FROM audit_event WHERE target_id=%s",
            (manifest["authorizationId"],),
        ).fetchall() == [{"actor_user": uuid.UUID(OWNER), "action": "RUN_EFFECTS_APPROVAL_ISSUED"}]
    revoked = client.post(
        url + "/revocation",
        json={"manifestDigest": manual_seal["manifestDigest"]},
        headers={CSRF_HEADER: csrf, "If-Match": "0"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revokedAt"] is not None
    assert client.get(url).json() == revoked.json()
    assert (
        client.post(
            url + "/revocation",
            json={"manifestDigest": manual_seal["manifestDigest"]},
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).json()
        == revoked.json()
    )
    assert (
        client.post(url, json=body, headers={CSRF_HEADER: csrf, "If-Match": "0"}).status_code == 409
    )
    with workspace_connection(db, WS) as conn:
        with pytest.raises(execution_approvals.Refused, match="revoked"):
            execution_approvals.assert_authorized(
                conn,
                sealed_manifest_id=manual_seal["sealedManifestId"],
                run_id=manifest["runId"],
                workspace_id=WS,
            )
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 1}
        assert conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE target_id=%s",
            (manifest["authorizationId"],),
        ).fetchone() == {"n": 2}


@pytest.mark.parametrize(
    "case",
    [
        "digest",
        "revision",
        "missing-revision",
        "past",
        "too-long",
        "malformed-expiry",
        "actor",
        "csrf",
        "viewer",
        "reviewer",
    ],
)
def test_manual_approval_refuses_wrong_scope_or_actor_without_recording_consent(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    manual_seal: dict[str, Any],
    case: str,
) -> None:
    from accessforge_api.auth import issue_session

    body = _approval_body(manual_seal)
    headers = {CSRF_HEADER: csrf, "If-Match": "0"}
    expected = 409
    if case == "digest":
        body["manifestDigest"] = "0" * 64
    elif case == "revision":
        headers["If-Match"] = "1"
    elif case == "missing-revision":
        del headers["If-Match"]
        expected = 428
    elif case == "past":
        body["expiresAt"] = "2000-01-01T00:00:00Z"
    elif case == "too-long":
        body["expiresAt"] = "2099-01-01T00:00:00Z"
    elif case == "malformed-expiry":
        body["expiresAt"] = "invalid"
        expected = 400
    elif case == "actor":
        body["actorId"] = OWNER
        expected = 400
    elif case == "csrf":
        del headers[CSRF_HEADER]
        expected = 403
    else:
        with workspace_connection(db, WS) as conn:
            if case == "reviewer":
                conn.execute(
                    "UPDATE workspace_membership SET role='REVIEWER' WHERE user_id=%s", (VIEWER,)
                )
            session = issue_session(conn, user_id=VIEWER)
        client.cookies.set(SESSION_COOKIE, session.session_token)
        headers[CSRF_HEADER] = session.csrf_token
        expected = 403
    response = client.post(_approval_url(project, manual_seal), json=body, headers=headers)
    assert response.status_code == expected, response.text
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 0}


@pytest.mark.parametrize("changed", ["expiry", "actor", "environment", "other-run"])
def test_manual_authority_is_reloaded_not_cached(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    environment: str,
    manual_seal: dict[str, Any],
    changed: str,
) -> None:
    assert (
        client.post(
            _approval_url(project, manual_seal),
            json=_approval_body(manual_seal),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    run_id = manual_seal["canonicalManifest"]["runId"]
    now = datetime.now(UTC)
    with workspace_connection(db, WS) as conn:
        if changed == "expiry":
            now += timedelta(minutes=20)
        elif changed == "actor":
            conn.execute("DELETE FROM workspace_membership WHERE user_id=%s", (OWNER,))
        elif changed == "environment":
            conn.execute(
                "UPDATE environment_manifest SET revoked_at=now() WHERE id=%s", (environment,)
            )
        else:
            run_id = str(uuid.uuid4())
        with pytest.raises((execution_approvals.Refused, project_store.ProjectError)):
            execution_approvals.assert_authorized(
                conn,
                sealed_manifest_id=manual_seal["sealedManifestId"],
                run_id=run_id,
                workspace_id=WS,
                now=now.isoformat().replace("+00:00", "Z"),
            )


def test_exact_approval_cannot_be_rewritten_deleted_or_unrevoked(
    db: str, client: TestClient, csrf: str, project: str, manual_seal: dict[str, Any]
) -> None:
    assert (
        client.post(
            _approval_url(project, manual_seal),
            json=_approval_body(manual_seal),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    approval_id = manual_seal["canonicalManifest"]["authorizationId"]
    with workspace_connection(db, WS) as conn:
        for assignment in (
            "target_digest=repeat('f',64)",
            "expected_revision=1",
            "expires_at=expires_at+interval '1 hour'",
            "scope='PATCH_APPLY'",
        ):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(f"UPDATE approval SET {assignment} WHERE id=%s", (approval_id,))  # noqa: S608
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("DELETE FROM approval WHERE id=%s", (approval_id,))
        conn.execute("UPDATE approval SET revoked_at=now() WHERE id=%s", (approval_id,))
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE approval SET revoked_at=NULL WHERE id=%s", (approval_id,))


@pytest.mark.parametrize(
    "field", ["scope", "target_id", "target_digest", "expected_revision", "expires_at"]
)
def test_database_refuses_misbound_reserved_approval_even_if_api_is_bypassed(
    db: str,
    manual_seal: dict[str, Any],
    field: str,
) -> None:
    from accessforge_domain.states import ApprovalScope
    from accessforge_persistence import approvals

    values: dict[str, Any] = {
        "workspace_id": WS,
        "actor_id": OWNER,
        "scope": ApprovalScope.RUN_EFFECTS,
        "target_id": manual_seal["sealedManifestId"],
        "target_digest": manual_seal["manifestDigest"],
        "expected_revision": 0,
        "expires_at": manual_seal["canonicalManifest"]["expiresAt"],
        "approval_id": manual_seal["canonicalManifest"]["authorizationId"],
    }
    replacements: dict[str, Any] = {
        "scope": ApprovalScope.PATCH_APPLY,
        "target_id": str(uuid.uuid4()),
        "target_digest": "0" * 64,
        "expected_revision": 1,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    values[field] = replacements[field]
    with workspace_connection(db, WS) as conn:
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            approvals.record_approval(conn, **values)
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 0}


def test_concurrent_issuance_cannot_duplicate_an_exact_decision(
    db: str, client: TestClient, csrf: str, project: str, manual_seal: dict[str, Any]
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    body = _approval_body(manual_seal)

    def issue() -> int:
        barrier.wait(timeout=5)
        return client.post(
            _approval_url(project, manual_seal),
            json=body,
            headers={CSRF_HEADER: csrf, "If-Match": "0", "Idempotency-Key": str(uuid.uuid4())},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(issue) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [201, 409]
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT count(*) AS n FROM approval").fetchone() == {"n": 1}
        assert conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE action='RUN_EFFECTS_APPROVAL_ISSUED'"
        ).fetchone() == {"n": 1}


def test_approval_routes_match_exact_project_and_workspace_and_allow_maintainers(
    db: str, client: TestClient, csrf: str, project: str, manual_seal: dict[str, Any]
) -> None:
    with workspace_connection(db, WS) as conn:
        conn.execute("UPDATE workspace_membership SET role='MAINTAINER' WHERE user_id=%s", (OWNER,))
        other_project = project_store.create_project(conn, workspace_id=WS, name="Other")
    assert (
        client.post(
            _approval_url(project, manual_seal),
            json=_approval_body(manual_seal),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    other_ws = str(uuid.uuid4())
    with unscoped_connection(db) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'Other')", (other_ws,))
    with workspace_connection(db, other_ws) as conn:
        conn.execute(
            "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES(%s,%s,'OWNER')",
            (other_ws, OWNER),
        )
        tenant_project = project_store.create_project(conn, workspace_id=other_ws, name="Tenant")
    for workspace, target_project in ((WS, other_project), (other_ws, tenant_project)):
        url = (
            f"/v1/workspaces/{workspace}/projects/{target_project}/seals/"
            f"{manual_seal['sealedManifestId']}/approval"
        )
        assert client.get(url).status_code == 404
        assert (
            client.post(
                url, json=_approval_body(manual_seal), headers={CSRF_HEADER: csrf, "If-Match": "0"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                url + "/revocation",
                json={"manifestDigest": manual_seal["manifestDigest"]},
                headers={CSRF_HEADER: csrf, "If-Match": "0"},
            ).status_code
            == 404
        )
    assert client.get(_approval_url(project, manual_seal)).json()["revokedAt"] is None


@pytest.mark.parametrize(
    "changed",
    [
        "none",
        "revoked",
        "lease-expiry",
        "lease-run",
        "profile",
        "cancelled",
        "dispatched",
        "preflight",
    ],
)
def test_manual_dispatch_survives_lease_revision_but_rechecks_exact_live_prerequisites(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    execution_body: dict[str, Any],
    runner: dict[str, Any],
    changed: str,
) -> None:
    """Synthetic preflight/desktop metadata tests the gate, never actual screen-reader readiness."""
    execution_body["runnerProfileDigest"] = runner["profileDigest"]
    created = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf},
    ).json()
    assert (
        client.post(
            _approval_url(project, created),
            json=_approval_body(created),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    manifest = created["canonicalManifest"]
    preflight = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(
            runner,
            manifestDigest=created["manifestDigest"],
            environmentConfigDigest=manifest["environmentConfigDigest"],
        ),
        headers={CSRF_HEADER: csrf},
    )
    assert preflight.status_code == 201, preflight.text
    admitted = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": created["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert admitted.status_code == 202, admitted.text
    run_id = manifest["runId"]
    with workspace_connection(db, WS) as conn:
        attempt = run_store.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        lease = runner_store.admit_lease(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt,
            runner_id=runner["runnerId"],
        )
    with workspace_connection(db, WS) as conn:
        assert conn.execute("SELECT revision FROM run WHERE id=%s", (run_id,)).fetchone() == {
            "revision": 1
        }
        runner_store.assert_manual_dispatch_authorized(
            conn,
            workspace_id=WS,
            run_id=run_id,
            runner_id=runner["runnerId"],
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        if changed == "none":
            return
        if changed == "revoked":
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=%s", (manifest["authorizationId"],)
            )
        elif changed == "lease-expiry":
            conn.execute(
                "UPDATE desktop_lease SET deadline_at=now()-interval '1 second' WHERE id=%s",
                (lease.lease_id,),
            )
        elif changed == "lease-run":
            other = run_store.create_run(conn, workspace_id=WS, manifest_digest="a" * 64)
            conn.execute("UPDATE desktop_lease SET run_id=%s WHERE id=%s", (other, lease.lease_id))
        elif changed == "profile":
            conn.execute(
                "UPDATE runner SET profile_digest=repeat('e',64) WHERE id=%s", (runner["runnerId"],)
            )
        elif changed == "cancelled":
            conn.execute(
                "UPDATE run SET cancel_requested_at=now(),cancellation_revision=revision "
                "WHERE id=%s",
                (run_id,),
            )
        elif changed == "dispatched":
            conn.execute("UPDATE run SET status='RUNNING' WHERE id=%s", (run_id,))
        else:
            conn.execute(
                "UPDATE runner_preflight SET manifest_digest=repeat('b',64) WHERE runner_id=%s",
                (runner["runnerId"],),
            )
        with pytest.raises(runner_store.DispatchRefused):
            runner_store.assert_manual_dispatch_authorized(
                conn,
                workspace_id=WS,
                run_id=run_id,
                runner_id=runner["runnerId"],
                lease_id=lease.lease_id,
                epoch=lease.epoch,
            )


def test_restored_exact_approval_is_revoked_and_cannot_be_revalidated_in_place(
    db: str,
    backup_database_url: str,
    client: TestClient,
    csrf: str,
    project: str,
    manual_seal: dict[str, Any],
) -> None:
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    from accessforge_persistence import connect, restore

    assert (
        client.post(
            _approval_url(project, manual_seal),
            json=_approval_body(manual_seal),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    snapshot = subprocess.run(  # noqa: S603 - fixed test command, no shell
        ["pg_dump", backup_database_url],  # noqa: S607 - operator-provisioned PostgreSQL client
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert snapshot.returncode == 0, "owned approval snapshot failed"
    # A later revocation is deliberately absent from the saved snapshot.
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE approval SET revoked_at=now() WHERE id=%s",
            (manual_seal["canonicalManifest"]["authorizationId"],),
        )
    name = "accessforge_manual_restore_" + uuid.uuid4().hex[:12]

    def database_url(url: str, database: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))

    admin = database_url(backup_database_url, "postgres")
    target_admin, target_app = database_url(backup_database_url, name), database_url(db, name)
    with connect(admin) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - exact owned generated name
    try:
        loaded = subprocess.run(  # noqa: S603 - fixed test command, no shell
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", target_admin],  # noqa: S607
            input=snapshot.stdout,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert loaded.returncode == 0, "owned approval restore failed"
        with workspace_connection(target_app, WS) as conn:
            # This is why a restored server must not serve until reconciliation completes.
            execution_approvals.assert_authorized(
                conn,
                sealed_manifest_id=manual_seal["sealedManifestId"],
                run_id=manual_seal["canonicalManifest"]["runId"],
                workspace_id=WS,
            )
        with connect(target_admin) as conn:
            report = restore.reconcile(conn, operator="owned-test", restore_id=str(uuid.uuid4()))
            assert report.execution_approvals_revoked == 1
            assert "Revoked 1 exact execution approvals" in report.summary
        with workspace_connection(target_app, WS) as conn:
            with pytest.raises(execution_approvals.Refused, match="revoked"):
                execution_approvals.assert_authorized(
                    conn,
                    sealed_manifest_id=manual_seal["sealedManifestId"],
                    run_id=manual_seal["canonicalManifest"]["runId"],
                    workspace_id=WS,
                )
            with pytest.raises(execution_approvals.Refused, match="already issued"):
                execution_approvals.issue(
                    conn,
                    sealed_manifest_id=manual_seal["sealedManifestId"],
                    actor_id=OWNER,
                    target_digest=manual_seal["manifestDigest"],
                    expected_revision=0,
                    expires_at=manual_seal["canonicalManifest"]["expiresAt"],
                )
    finally:
        with connect(admin) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')  # noqa: S608 - owned name only


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


# --- provenance cannot be misreported ------------------------------------------------------------


def test_a_clean_tree_cannot_be_recorded_with_differing_paths(
    client: TestClient, csrf: str, project: str
) -> None:
    """Two halves of one fact, and they were read independently.

    `{"dirtyPaths": ["a.ts"]}` with `dirty` omitted recorded a snapshot saying a clean tree had one
    differing path — and that record travels into every seal built on the build, which is precisely
    what the route's own docstring forbids.
    """
    refused = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(dirty=False, dirtyPaths=["src/app.ts"]),
        headers={CSRF_HEADER: csrf},
    )
    assert refused.status_code == 400, refused.text
    assert "they describe the same fact and disagree" in refused.json()["detail"]

    # The other direction is a contradiction too: dirty with nothing to point at.
    assert (
        client.post(
            f"/v1/workspaces/{WS}/projects/{project}/builds",
            json=_build_body(dirty=True, dirtyPaths=[]),
            headers={CSRF_HEADER: csrf},
        ).status_code
        == 400
    )


def test_omitting_dirty_derives_it_from_the_paths(
    client: TestClient, csrf: str, project: str
) -> None:
    """Derived rather than defaulted to clean, which is the safe direction.

    Refusing the contradiction and defaulting the omission are different choices on purpose: a
    caller who said "clean" while listing paths disagrees with themselves and should be told, and a
    caller who said nothing should get the truth rather than the convenient answer.
    """
    body = _build_body(dirtyPaths=["src/app.ts", "README.md"])
    del body["dirty"]
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=body,
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201, response.text
    assert response.json()["dirty"] is True
    assert response.json()["dirtyPathCount"] == 2


def test_a_seal_cannot_use_another_projects_build_or_environment(
    db: str, client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """Row-level security scopes by workspace, not by project.

    The foreign keys are composite on (id, workspace_id), so neither stops a caller authorized for
    project A from sealing project B's artifact into a manifest whose `project_id` is A. Runs
    requested with that digest would carry provenance naming a project that never built the
    artifact.
    """
    with workspace_connection(db, WS) as conn:
        other_project = project_store.create_project(conn, workspace_id=WS, name="Other project")

    other_environment = client.post(
        f"/v1/workspaces/{WS}/projects/{other_project}/environments",
        json={
            "name": "staging",
            "allowedOrigins": ["https://other.example.test"],
            "fixtureResetStrategy": "RESET_ENDPOINT",
            "observerCredentialRef": "observer-profile",
            "resetCredentialRef": "reset-profile",
            "permittedEffects": ["FORM_SUBMIT"],
            "expiresAt": (datetime.now(UTC) + timedelta(days=30))
            .isoformat()
            .replace("+00:00", "Z"),
        },
        headers={CSRF_HEADER: csrf},
    ).json()["environmentId"]
    other_build = client.post(
        f"/v1/workspaces/{WS}/projects/{other_project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()["buildId"]
    own_build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()["buildId"]

    borrowed_build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(other_build, environment),
        headers={CSRF_HEADER: csrf},
    )
    assert borrowed_build.status_code == 400, borrowed_build.text
    assert "belongs to a different project" in borrowed_build.json()["detail"]

    borrowed_environment = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=_seal_body(own_build, other_environment),
        headers={CSRF_HEADER: csrf},
    )
    assert borrowed_environment.status_code == 400, borrowed_environment.text
    assert "environment belongs to a different project" in borrowed_environment.json()["detail"]


def test_canonical_run_admission_preserves_reserved_identity_and_refuses_reuse(
    db: str, client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """Actual HTTP authorization/admission; deliberately synthetic execution input records."""
    build = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/builds",
        json=_build_body(),
        headers={CSRF_HEADER: csrf},
    ).json()
    run_id, approval_id, journey_id = (str(uuid.uuid4()) for _ in range(3))
    inputs = project_store.SealInputs(
        "a" * 64, "a" * 64, "a" * 64, "a" * 64, "a" * 64, "synthetic", "b" * 64
    )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO journey_version(id,workspace_id,project_id,name,platform,journey_digest,"
            "assertion_set_digest,fixture_digest,navigator_policy_digest,navigator_policy,"
            "reviewer_summary) VALUES (%s,%s,%s,'synthetic','web',repeat('a',64),repeat('a',64),"
            "repeat('a',64),repeat('a',64),'{}','{}')",
            (journey_id, WS, project),
        )
        sealed = project_store.seal_run(
            conn,
            workspace_id=WS,
            project_id=project,
            source_snapshot_id=build["sourceSnapshotId"],
            build_artifact_id=build["buildId"],
            environment_manifest_id=environment,
            inputs=inputs,
            run_id=run_id,
            authorization_id=approval_id,
            execution=project_store.ExecutionInputs(
                journey_id,
                (datetime.now(UTC) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                10,
                20,
                frozenset({"FORM_SUBMIT"}),
            ),
        )
    body = {"manifestDigest": sealed.manifest_digest}
    wrong = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={**body, "authorizationId": str(uuid.uuid4())},
        headers={CSRF_HEADER: csrf},
    )
    assert wrong.status_code == 400
    assert "authorizationId differs" in wrong.json()["detail"]
    admitted = client.post(f"/v1/workspaces/{WS}/runs", json=body, headers={CSRF_HEADER: csrf})
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["runId"] == run_id
    with workspace_connection(db, WS) as conn:
        assert conn.execute(
            "SELECT authorization_id FROM run WHERE id=%s", (run_id,)
        ).fetchone() == {"authorization_id": uuid.UUID(approval_id)}
        assert conn.execute("SELECT 1 FROM approval WHERE id=%s", (approval_id,)).fetchone() is None
    duplicate = client.post(f"/v1/workspaces/{WS}/runs", json=body, headers={CSRF_HEADER: csrf})
    assert duplicate.status_code == 400, duplicate.text
    assert "already belongs" in duplicate.json()["detail"]


def test_a_run_records_the_project_that_sealed_its_manifest(
    db: str, client: TestClient, csrf: str, project: str, environment: str
) -> None:
    """A run's provenance is its manifest's, not its caller's.

    Two holes here. A caller could name project A while using a manifest project B sealed — false
    provenance on a run whose identity says otherwise — and a caller who omitted `projectId` stored
    NULL, so the run had no project at all while its manifest plainly belonged to one.
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
    ).json()

    with workspace_connection(db, WS) as conn:
        other_project = project_store.create_project(conn, workspace_id=WS, name="Bystander")

    mismatched = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"projectId": other_project, "manifestDigest": sealed["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert mismatched.status_code == 400, mismatched.text
    assert "does not match the project that sealed this manifest" in mismatched.json()["detail"]

    # Omitted, and the run still records the sealing project rather than NULL.
    requested = client.post(
        f"/v1/workspaces/{WS}/runs",
        json={"manifestDigest": sealed["manifestDigest"]},
        headers={CSRF_HEADER: csrf},
    )
    assert requested.status_code == 202, requested.text
    with workspace_connection(db, WS) as conn:
        row = conn.execute(
            "SELECT project_id FROM run WHERE id = %s", (requested.json()["runId"],)
        ).fetchone()
    assert row is not None
    assert str(row["project_id"]) == project


def test_submitting_a_preflight_needs_runner_management_not_run_request(
    db: str, client: TestClient, runner: dict[str, Any]
) -> None:
    """A preflight decides whether a desktop becomes READY, which is runner management.

    `RUN_REQUEST` is held by anyone who may ask for a run. Letting that role flip a runner to READY
    would make the permission to request work also the permission to declare the machine fit to do
    it. In production the runner submits its own preflight under a service credential; that
    principal does not exist yet, so this human-facing route takes the stricter permission.
    """
    from accessforge_api.auth import issue_session
    from accessforge_domain.authorization.roles import Permission, Role, permissions_for

    # The premise, asserted rather than assumed: these are genuinely different permissions, and the
    # role under test holds one and not the other.
    assert Permission.RUN_REQUEST in permissions_for(Role.MAINTAINER)
    assert Permission.INFRASTRUCTURE_OPERATE not in permissions_for(Role.MAINTAINER)

    maintainer = str(uuid.UUID(int=0x353))
    with unscoped_connection(db) as conn:
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, 'maintainer@example.test')", (maintainer,)
        )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s,%s,'MAINTAINER')",
            (WS, maintainer),
        )
        issued = issue_session(conn, user_id=maintainer)
    client.cookies.set(SESSION_COOKIE, issued.session_token)

    response = client.post(
        f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
        json=_preflight_body(runner),
        headers={CSRF_HEADER: issued.csrf_token},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "PERMISSION_DENIED"
