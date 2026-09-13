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

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE
from accessforge_api.config import ApiSettings
from accessforge_contracts import validate
from accessforge_domain.canonical import digest
from accessforge_orchestrator.manual_dispatch import (
    DispatchReference,
    HandoffUnknown,
    ManualRunController,
    ReaderTransportUnavailable,
)
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
from accessforge_persistence.supervisor_dispatch import DispatchTicket

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
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    environment: str,
    request: pytest.FixtureRequest,
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
    policy: dict[str, Any] = {}
    reviewer_summary: dict[str, Any] = {}
    if getattr(request, "param", None) in {"action-policy", "stop-policy"}:
        policy = {
            "allowedActions": ["READ_CURRENT", "NEXT", "TYPE_TEXT", "KEY_CHORD"],
            "allowedKeyChords": [],
            "maxActions": 10,
            "wallTimeSeconds": 30,
        }
        body["navigatorPolicyDigest"] = str(digest(policy))
        if request.param == "stop-policy":
            policy["allowedActions"].append("STOP")
            body["navigatorPolicyDigest"] = str(digest(policy))
            from accessforge_domain.journeys.assertions import (
                Assertion,
                AssertionKind,
                AssertionSet,
                EvaluationRule,
                UnknownReason,
            )

            assertions = AssertionSet(
                (
                    Assertion(
                        "completion.one-request",
                        AssertionKind.TASK_COMPLETION,
                        "One independently measured request",
                        unknown_reasons=frozenset({UnknownReason.OBSERVER_UNREACHABLE}),
                        evaluation_rule=EvaluationRule(
                            "EFFECT_COUNT", effect="CREATE_TEST_REQUEST", count=1
                        ),
                    ),
                )
            )
            reviewer_summary["assertionContract"] = assertions.canonical_form()
            body["assertionSetDigest"] = str(digest(assertions.canonical_form()))
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO journey_version(id,workspace_id,project_id,name,platform,journey_digest,"
            "assertion_set_digest,fixture_digest,navigator_policy_digest,navigator_policy,"
            "reviewer_summary) VALUES (%s,%s,%s,'synthetic','web',%s,%s,%s,%s,%s,%s)",
            (
                journey_id,
                WS,
                project,
                body["journeyDigest"],
                body["assertionSetDigest"],
                body["fixtureDigest"],
                body["navigatorPolicyDigest"],
                json.dumps(policy),
                json.dumps(reviewer_summary),
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


@pytest.fixture()
def manual_dispatch_reference(
    db: str,
    client: TestClient,
    csrf: str,
    project: str,
    execution_body: dict[str, Any],
    runner: dict[str, Any],
) -> DispatchReference:
    """Synthetic desktop/preflight, real API consent and PostgreSQL lease. Not actual AT proof."""
    execution_body["runnerProfileDigest"] = runner["profileDigest"]
    response = client.post(
        f"/v1/workspaces/{WS}/projects/{project}/seals",
        json=execution_body,
        headers={CSRF_HEADER: csrf},
    )
    assert response.status_code == 201
    created = response.json()
    assert (
        client.post(
            _approval_url(project, created),
            json=_approval_body(created),
            headers={CSRF_HEADER: csrf, "If-Match": "0"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/v1/workspaces/{WS}/runners/{runner['runnerId']}/preflights",
            json=_preflight_body(
                runner,
                manifestDigest=created["manifestDigest"],
                environmentConfigDigest=created["canonicalManifest"]["environmentConfigDigest"],
            ),
            headers={CSRF_HEADER: csrf},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/v1/workspaces/{WS}/runs",
            json={"manifestDigest": created["manifestDigest"]},
            headers={CSRF_HEADER: csrf},
        ).status_code
        == 202
    )
    run_id = created["canonicalManifest"]["runId"]
    with workspace_connection(db, WS) as conn:
        attempt_id = run_store.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        lease = runner_store.admit_lease(
            conn,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            runner_id=runner["runnerId"],
        )
    return DispatchReference(
        WS, run_id, attempt_id, runner["runnerId"], lease.lease_id, lease.epoch
    )


class SyntheticStartTransport:
    """Does not touch a reader, model, fixture or desktop."""

    def __init__(self, db: str) -> None:
        self.db = db
        self.calls = 0
        self.ticket: DispatchTicket | None = None

    def check_available(self) -> None:
        pass

    async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
        self.calls += 1
        self.ticket = ticket
        # A separate connection must observe the committed claim BEFORE any transport work.
        with workspace_connection(self.db, WS) as conn:
            state = run_store.load_run(conn, run_id=reference.run_id).state
            assert state.status.value == "RUNNING"
            assert state.outcome.value == "NOT_EVALUATED"
            claim = conn.execute(
                "SELECT detail FROM audit_event WHERE target_id=%s "
                "AND action='MANUAL_DISPATCH_CLAIMED'",
                (reference.run_id,),
            ).fetchone()
            assert claim is not None and claim["detail"]["context"] == asdict(reference)
            actions = conn.execute("SELECT count(*) AS n FROM runner_action").fetchone()
            assert actions is not None and actions["n"] == 0


@pytest.mark.asyncio
async def test_manual_controller_commits_before_send_and_never_replays(
    db: str,
    manual_dispatch_reference: DispatchReference,
) -> None:
    ref = manual_dispatch_reference
    transport = SyntheticStartTransport(db)
    controller = ManualRunController(db, transport)
    assert await controller.dispatch(ref, expected_revision=1) == ref
    with pytest.raises(run_store.StaleRevision):
        await controller.dispatch(ref, expected_revision=1)
    with pytest.raises(runner_store.DispatchRefused):
        await controller.dispatch(ref, expected_revision=2)
    assert transport.calls == 1
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert state.outcome.value == "NOT_EVALUATED"
        assert state.status.value == "RUNNING"
        message = conn.execute(
            "SELECT count(*) AS n FROM outbox_message WHERE topic='run.running'"
        ).fetchone()
        assert message is not None and message["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        "default-unavailable",
        "revoked",
        "cancelled",
        "attempt",
        "workspace",
        "epoch",
        "revision",
        "lease",
        "timeout-setting",
    ],
)
async def test_manual_controller_refusals_never_reach_transport(
    db: str,
    manual_dispatch_reference: DispatchReference,
    changed: str,
) -> None:
    ref = manual_dispatch_reference
    transport = SyntheticStartTransport(db)
    controller = ManualRunController(db, transport)
    revision = 1
    if changed == "default-unavailable":
        controller = ManualRunController(db)
    elif changed == "revoked":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id="
                "(SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    elif changed == "cancelled":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE run SET cancel_requested_at=now(),cancellation_revision=revision "
                "WHERE id=%s",
                (ref.run_id,),
            )
    elif changed == "attempt":
        ref = replace(ref, attempt_id=str(uuid.uuid4()))
    elif changed == "workspace":
        ref = replace(ref, workspace_id=str(uuid.uuid4()))
    elif changed == "epoch":
        ref = replace(ref, epoch=2)
    elif changed == "lease":
        ref = replace(ref, lease_id=str(uuid.uuid4()))
    elif changed == "revision":
        revision = 0
    else:
        controller = ManualRunController(db, transport, float("nan"))
    with pytest.raises(
        (
            runner_store.DispatchRefused,
            run_store.StaleRevision,
            LookupError,
            ReaderTransportUnavailable,
            ValueError,
        )
    ):
        await controller.dispatch(ref, expected_revision=revision)
    assert transport.calls == 0
    with workspace_connection(db, WS) as conn:
        assert (
            run_store.load_run(conn, run_id=manual_dispatch_reference.run_id).state.status.value
            == "LEASED"
        )
        assert (
            conn.execute(
                "SELECT 1 FROM audit_event WHERE action='MANUAL_DISPATCH_CLAIMED'"
            ).fetchone()
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "timeout", "cancelled"])
async def test_manual_handoff_uncertainty_interrupts_and_quarantines_without_stop_proof(
    db: str,
    manual_dispatch_reference: DispatchReference,
    failure: str,
) -> None:
    class FailingTransport(SyntheticStartTransport):
        async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
            await super().start(reference, ticket=ticket)
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "cancelled":
                raise asyncio.CancelledError()
            raise RuntimeError("untrusted transport details must not be surfaced")

    ref = manual_dispatch_reference
    transport = FailingTransport(db)
    controller = ManualRunController(db, transport, 0.05)
    expected = asyncio.CancelledError if failure == "cancelled" else HandoffUnknown
    with pytest.raises(expected):
        await controller.dispatch(ref, expected_revision=1)
    controller.interrupt(ref)  # exact recovery is idempotent, never resumes the attempt
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert (state.status.value, state.outcome.value) == ("INTERRUPTED", "INCONCLUSIVE")
        assert state.stop_acknowledged_at is None
        assert state.quarantined and state.ambiguity_reason == "MANUAL_HANDOFF_UNKNOWN"
        runner_row = conn.execute(
            "SELECT status FROM runner WHERE id=%s", (ref.runner_id,)
        ).fetchone()
        assert runner_row is not None and runner_row["status"] == "QUARANTINED"
        lease_row = conn.execute(
            "SELECT released_at,stop_acknowledged_at FROM desktop_lease WHERE id=%s",
            (ref.lease_id,),
        ).fetchone()
        assert lease_row is not None and lease_row["stop_acknowledged_at"] is None
        assert lease_row["released_at"] is not None
    with pytest.raises(run_store.StaleRevision):
        await controller.dispatch(ref, expected_revision=1)
    assert transport.calls == 1


def test_two_manual_controllers_race_one_committed_handoff(
    db: str,
    manual_dispatch_reference: DispatchReference,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    transport = SyntheticStartTransport(db)

    def contend() -> str:
        barrier.wait(timeout=5)
        try:
            asyncio.run(
                ManualRunController(db, transport).dispatch(
                    manual_dispatch_reference, expected_revision=1
                )
            )
            return "sent"
        except run_store.StaleRevision:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: contend(), range(2)))
    assert sorted(results) == ["sent", "stale"]
    assert transport.calls == 1


def test_process_dies_after_dispatch_commit_no_restart_replay(
    db: str,
    manual_dispatch_reference: DispatchReference,
) -> None:
    import json
    import subprocess
    import sys

    ref = manual_dispatch_reference
    # Real abrupt process death, no exception handler/finally rollback or graceful quarantine.
    program = """
import asyncio, json, os
from accessforge_orchestrator.manual_dispatch import DispatchReference, ManualRunController
class CrashTransport:
    def check_available(self): pass
    async def start(self, reference, *, ticket): os._exit(24)
asyncio.run(ManualRunController(os.environ['AF_CONTROLLER_TEST_DB'], CrashTransport()).dispatch(
    DispatchReference(**json.loads(os.environ['AF_CONTROLLER_TEST_REF'])), expected_revision=1))
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter/program; fixture IDs via environment
        [sys.executable, "-c", program],
        env={
            **os.environ,
            "AF_CONTROLLER_TEST_DB": db,
            "AF_CONTROLLER_TEST_REF": json.dumps(asdict(ref)),
        },
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 24
    transport = SyntheticStartTransport(db)
    controller = ManualRunController(db, transport)
    with pytest.raises(run_store.StaleRevision):
        asyncio.run(controller.dispatch(ref, expected_revision=1))
    assert transport.calls == 0
    with workspace_connection(db, WS) as conn:
        assert run_store.load_run(conn, run_id=ref.run_id).state.status.value == "RUNNING"
    controller.interrupt(ref)
    with workspace_connection(db, WS) as conn:
        assert run_store.load_run(conn, run_id=ref.run_id).state.status.value == "INTERRUPTED"


@pytest.mark.asyncio
async def test_late_transport_success_does_not_clear_unknown_or_quarantine(
    db: str,
    manual_dispatch_reference: DispatchReference,
) -> None:
    release = asyncio.Event()
    finished = asyncio.Event()

    class LateTransport(SyntheticStartTransport):
        async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
            await super().start(reference, ticket=ticket)
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()  # A remote receiver may ignore cancellation.
            finally:
                finished.set()

    ref = manual_dispatch_reference
    transport = LateTransport(db)
    controller = ManualRunController(db, transport, 0.05)
    with pytest.raises(HandoffUnknown):
        await controller.dispatch(ref, expected_revision=1)
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=2)
    with workspace_connection(db, WS) as conn:
        assert run_store.load_run(conn, run_id=ref.run_id).state.status.value == "INTERRUPTED"
        runner_row = conn.execute(
            "SELECT status FROM runner WHERE id=%s", (ref.runner_id,)
        ).fetchone()
        assert runner_row is not None and runner_row["status"] == "QUARANTINED"
    assert transport.calls == 1


@pytest.mark.parametrize("changed", ["no-claim", "attempt", "lease", "epoch", "runner"])
def test_manual_recovery_refuses_unclaimed_or_mismatched_attempt(
    db: str,
    manual_dispatch_reference: DispatchReference,
    changed: str,
) -> None:
    ref = manual_dispatch_reference
    controller = ManualRunController(db, SyntheticStartTransport(db))
    if changed != "no-claim":
        asyncio.run(controller.dispatch(ref, expected_revision=1))
        if changed == "epoch":
            ref = replace(ref, epoch=2)
        elif changed == "attempt":
            ref = replace(ref, attempt_id=str(uuid.uuid4()))
        elif changed == "lease":
            ref = replace(ref, lease_id=str(uuid.uuid4()))
        else:
            ref = replace(ref, runner_id=str(uuid.uuid4()))
    with pytest.raises(runner_store.DispatchRefused):
        controller.interrupt(ref)
    with workspace_connection(db, WS) as conn:
        runner_row = conn.execute(
            "SELECT status FROM runner WHERE id=%s", (manual_dispatch_reference.runner_id,)
        ).fetchone()
        assert runner_row is not None and runner_row["status"] == "BUSY"


@pytest.mark.asyncio
async def test_dispatch_transaction_failure_rolls_back_and_never_sends(
    db: str,
    manual_dispatch_reference: DispatchReference,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = run_store.apply_transition

    def fail_after_transition(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure before commit")

    monkeypatch.setattr(run_store, "apply_transition", fail_after_transition)
    transport = SyntheticStartTransport(db)
    with pytest.raises(RuntimeError, match="before commit"):
        await ManualRunController(db, transport).dispatch(
            manual_dispatch_reference, expected_revision=1
        )
    assert transport.calls == 0
    with workspace_connection(db, WS) as conn:
        assert (
            run_store.load_run(conn, run_id=manual_dispatch_reference.run_id).state.status.value
            == "LEASED"
        )
        assert (
            conn.execute(
                "SELECT 1 FROM audit_event WHERE action='MANUAL_DISPATCH_CLAIMED'"
            ).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT 1 FROM outbox_message WHERE topic='run.running'").fetchone()
            is None
        )


@pytest.fixture()
def supervisor_ticket(db: str, manual_dispatch_reference: DispatchReference) -> DispatchTicket:
    transport = SyntheticStartTransport(db)
    asyncio.run(
        ManualRunController(db, transport).dispatch(manual_dispatch_reference, expected_revision=1)
    )
    assert transport.ticket is not None
    return transport.ticket


def _ticket_url(ticket: DispatchTicket, workspace_id: str = WS) -> str:
    return f"/v1/workspaces/{workspace_id}/supervisor-dispatches/{ticket.ticket_id}/accept"


def _seed_reader_fixture(db: str, ref: DispatchReference) -> None:
    from accessforge_persistence.fixtures import create_instance

    with workspace_connection(db, WS) as conn:
        create_instance(
            conn,
            workspace_id=WS,
            run_id=ref.run_id,
            template_id="reader-wire-test",
            template_digest=_digest("reader-wire-test"),
            navigator_values={"name": "Synthetic Private Fixture"},
            observer_config={"receipt": "synthetic-private-receipt"},
        )


@pytest.mark.parametrize("execution_body", ["action-policy"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "bootstrap-reuse",
        "session-revoked",
        "parent-revoked",
        "approval-revoked",
        "wrong-origin",
        "sequence",
        "unknown-field",
    ],
)
def test_supervisor_session_action_intent(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    fault: str | None,
) -> None:
    import hashlib
    import secrets

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    secret = secrets.token_urlsafe(32)
    url = _ticket_url(ticket).removesuffix("accept") + "session"
    # A browser OWNER cannot open a machine session, and the response never returns either secret.
    assert client.post(url, json={"sessionSecret": secret}).status_code == 401
    response = client.post(
        url, json={"sessionSecret": secret}, headers={"Authorization": f"Bearer {ticket.token}"}
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    receipt = response.json()
    assert receipt["sessionId"] == ticket.ticket_id and receipt["attemptId"] == ref.attempt_id
    assert receipt["meaning"] == "SUPERVISOR_SESSION_OPENED"
    assert secret not in response.text and ticket.token not in response.text
    assert (
        client.post(
            url, json={"sessionSecret": secret}, headers={"Authorization": f"Bearer {ticket.token}"}
        ).status_code
        == 401
    )
    with workspace_connection(db, WS) as conn:
        session = conn.execute(
            "SELECT token_digest FROM supervisor_execution_session WHERE ticket_id=%s",
            (ticket.ticket_id,),
        ).fetchone()
        assert (
            session is not None
            and session["token_digest"] == hashlib.sha256(secret.encode()).hexdigest()
        )
        if fault == "session-revoked":
            conn.execute(
                "UPDATE supervisor_execution_session SET revoked_at=now() WHERE ticket_id=%s",
                (ticket.ticket_id,),
            )
        if fault == "parent-revoked":
            conn.execute(
                "UPDATE supervisor_dispatch_ticket SET revoked_at=now() WHERE id=%s",
                (ticket.ticket_id,),
            )
        if fault == "approval-revoked":
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=("
                "SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    command: dict[str, Any] = {
        "action": "READ_CURRENT",
        "sequence": 1,
        "origin": "https://app.example.test",
    }
    if fault == "wrong-origin":
        command["origin"] = "https://unapproved.example"
    if fault == "sequence":
        command["sequence"] = 2
    if fault == "unknown-field":
        command["selector"] = "body"
    headers = {"Authorization": f"Bearer {ticket.token if fault == 'bootstrap-reuse' else secret}"}
    action_url = f"/v1/workspaces/{WS}/supervisor-sessions/{ticket.ticket_id}/action-intents"
    assert client.post(action_url, json=command).status_code == 401
    intended = client.post(action_url, json=command, headers=headers)
    assert intended.status_code == (201 if fault is None else 403)
    if fault is None:
        assert intended.json()["meaning"] == "ACTION_INTENT_RETAINED"
        assert client.post(action_url, json=command, headers=headers).status_code == 403
        assert (
            client.post(action_url, json={**command, "sequence": 2}, headers=headers).status_code
            == 403
        )
    with workspace_connection(db, WS) as conn:
        actions = conn.execute(
            "SELECT * FROM runner_action WHERE run_id=%s", (ref.run_id,)
        ).fetchall()
        assert len(actions) == (1 if fault is None else 0)
        for action in actions:
            assert action["dispatched_at"] is None and action["result_at"] is None
        assert conn.execute("SELECT count(*) AS n FROM canonical_event").fetchone() == {
            "n": 3 if fault is None else 2,
        }


@pytest.mark.parametrize("execution_body", ["action-policy"], indirect=True)
@pytest.mark.parametrize(
    "case", ["success", "ambiguity", "before-dispatch", "revoked", "wrong-action", "origin"]
)
def test_authenticated_action_dispatch_and_result(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    case: str,
) -> None:
    import secrets

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    secret = secrets.token_urlsafe(32)
    opened = client.post(
        _ticket_url(ticket).removesuffix("accept") + "session",
        json={"sessionSecret": secret},
        headers={"Authorization": f"Bearer {ticket.token}"},
    )
    assert opened.status_code == 201
    headers = {"Authorization": f"Bearer {secret}"}
    base = f"/v1/workspaces/{WS}/supervisor-sessions/{ticket.ticket_id}"
    command = {"action": "READ_CURRENT", "origin": "https://app.example.test", "sequence": 1}
    intent = client.post(base + "/action-intents", json=command, headers=headers)
    assert intent.status_code == 201
    action_id = intent.json()["actionId"]
    action_url = base + f"/actions/{action_id}"
    if case == "before-dispatch":
        assert (
            client.post(
                action_url + "/result", json={"status": "SUCCEEDED"}, headers=headers
            ).status_code
            == 403
        )
        return
    if case == "revoked":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=("
                "SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    if case == "wrong-action":
        action_url = base + f"/actions/{uuid.uuid4()}"
    dispatched = client.post(
        action_url + "/dispatch",
        headers=headers,
        json={"origin": "https://other.example.test" if case == "origin" else command["origin"]},
    )
    if case in {"revoked", "wrong-action", "origin"}:
        assert dispatched.status_code == 403
        with workspace_connection(db, WS) as conn:
            assert conn.execute(
                "SELECT dispatched_at FROM runner_action WHERE id=%s", (action_id,)
            ).fetchone() == {"dispatched_at": None}
        return
    assert dispatched.status_code == 200
    assert dispatched.json() == {
        "sessionId": ticket.ticket_id,
        "meaning": "ACTION_DISPATCH_COMMITTED",
        "command": {"actionId": action_id, "action": "READ_CURRENT", "sequence": 1},
    }
    assert (
        client.post(
            action_url + "/dispatch", json={"origin": command["origin"]}, headers=headers
        ).status_code
        == 403
    )
    status = "AMBIGUOUS" if case == "ambiguity" else "SUCCEEDED"
    result = client.post(action_url + "/result", json={"status": status}, headers=headers)
    assert result.status_code == 200 and result.json()["meaning"] == "ACTION_RESULT_RETAINED"
    assert (
        client.post(
            action_url + "/result", json={"status": "SUCCEEDED"}, headers=headers
        ).status_code
        == 403
    )
    second = client.post(base + "/action-intents", json={**command, "sequence": 2}, headers=headers)
    assert second.status_code == (403 if case == "ambiguity" else 201)
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert (state.status.value, state.outcome.value) == (
            ("INTERRUPTED", "INCONCLUSIVE") if case == "ambiguity" else ("RUNNING", "NOT_EVALUATED")
        )
        if case == "ambiguity":
            assert conn.execute(
                "SELECT status FROM runner WHERE id=%s", (ref.runner_id,)
            ).fetchone() == {"status": "QUARANTINED"}
        assert conn.execute("SELECT count(*) AS n FROM canonical_event").fetchone() == {
            "n": 4 if case == "ambiguity" else 5,
        }


@pytest.mark.parametrize(
    "scenario",
    [
        "fresh",
        "expired-ticket",
        "accepted-expired-ticket",
        "expired-lease",
        "revoked-ticket",
        "stop-proof",
        "stale-epoch",
    ],
)
def test_automatic_handoff_recovery(
    db: str,
    manual_dispatch_reference: DispatchReference,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    from accessforge_domain import reducers
    from accessforge_orchestrator.maintenance.handoff_worker import recover_once
    from accessforge_persistence import supervisor_dispatch

    ref = manual_dispatch_reference

    # Reproduce a controller process dying immediately AFTER its commit: the exact same transition,
    # audit and ticket issue, but no post-commit transport or in-process compensation can execute.
    # The aged clock is confined to initial ticket issuance; no immutable stored row is rewritten.
    class Earlier(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Earlier:
            return cls.fromtimestamp((datetime.now(tz) - timedelta(seconds=31)).timestamp(), tz)

    with workspace_connection(db, WS) as conn:
        run_store.apply_transition(
            conn,
            run_id=ref.run_id,
            reducer=reducers.progress,
            expected_revision=1,
            operation_id=str(uuid.uuid4()),
            topic="run.running",
            actor_service="manual-run-controller",
            audit_action="MANUAL_DISPATCH_CLAIMED",
            audit_context=asdict(ref),
        )
        with monkeypatch.context() as patch:
            if scenario in {"expired-ticket", "accepted-expired-ticket"}:
                patch.setattr(supervisor_dispatch, "datetime", Earlier)
            ticket = supervisor_dispatch.issue(conn, **asdict(ref))
        if scenario == "accepted-expired-ticket":
            # Synthetic historical acceptance, not actual reader proof. Receipt expiry after
            # consumption must not be confused with the still-live desktop lease deadline.
            conn.execute(
                "UPDATE supervisor_dispatch_ticket SET accepted_at=created_at+interval '1 second' "
                "WHERE id=%s",
                (ticket.ticket_id,),
            )
        if scenario in {"expired-lease", "stop-proof", "stale-epoch"}:
            conn.execute(
                "UPDATE desktop_lease SET deadline_at=now()-interval '1 second' WHERE id=%s",
                (ref.lease_id,),
            )
        if scenario == "revoked-ticket":
            conn.execute(
                "UPDATE supervisor_dispatch_ticket SET revoked_at=now() WHERE id=%s",
                (ticket.ticket_id,),
            )
        if scenario == "stop-proof":
            conn.execute(
                "UPDATE desktop_lease SET stop_acknowledged_at=now(),stop_acknowledged_epoch=epoch "
                "WHERE id=%s",
                (ref.lease_id,),
            )
        if scenario == "stale-epoch":
            conn.execute(
                "UPDATE runner SET lease_epoch=lease_epoch+1 WHERE id=%s", (ref.runner_id,)
            )
    should_recover = scenario in {"expired-ticket", "expired-lease", "revoked-ticket"}
    # Explicit workspace scope: a different tenant cannot discover or recover this attempt.
    assert recover_once(db, str(uuid.uuid4())).interrupted == 0
    if scenario == "expired-lease":
        process = subprocess.run(  # noqa: S603 - actual scoped recovery CLI, no shell
            [
                sys.executable,
                "-m",
                "accessforge_orchestrator.maintenance.handoff_worker",
                "--workspace-id",
                WS,
                "--once",
            ],
            env={"PATH": os.environ["PATH"], "ACCESSFORGE_DATABASE_URL": db},
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert process.returncode == 0
        assert "interrupted=1 deferred=0" in process.stderr
        assert db not in process.stderr + process.stdout
    else:
        first = recover_once(db, WS)
        assert first.interrupted == int(should_recover) and first.deferred == 0
    assert recover_once(db, WS).interrupted == 0
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert (state.status.value, state.outcome.value) == (
            ("INTERRUPTED", "INCONCLUSIVE") if should_recover else ("RUNNING", "NOT_EVALUATED")
        )
        runner = conn.execute("SELECT status FROM runner WHERE id=%s", (ref.runner_id,)).fetchone()
        assert runner is not None
        assert runner["status"] == ("QUARANTINED" if should_recover else "BUSY")
        audit = conn.execute(
            "SELECT 1 FROM audit_event WHERE action='RUN_INTERRUPTED_MANUAL_HANDOFF_UNKNOWN'"
        ).fetchall()
        assert len(audit) == int(should_recover)
        assert conn.execute("SELECT count(*) AS n FROM runner_action").fetchone() == {"n": 0}
        if should_recover:
            row = conn.execute(
                "SELECT revoked_at FROM supervisor_dispatch_ticket WHERE id=%s", (ticket.ticket_id,)
            ).fetchone()
            assert row is not None and row["revoked_at"] is not None


@pytest.mark.parametrize("execution_body", ["action-policy"], indirect=True)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "digest",
        "gap",
        "identity",
        "before-dispatch",
        "revoked",
        "extra-field",
        "oversize",
    ],
)
def test_authenticated_reader_evidence(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    fault: str | None,
) -> None:
    import secrets

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    _seed_reader_fixture(db, ref)
    secret = secrets.token_urlsafe(32)
    assert (
        client.post(
            _ticket_url(ticket).removesuffix("accept") + "session",
            json={"sessionSecret": secret},
            headers={"Authorization": f"Bearer {ticket.token}"},
        ).status_code
        == 201
    )
    headers = {"Authorization": f"Bearer {secret}"}
    base = f"/v1/workspaces/{WS}/supervisor-sessions/{ticket.ticket_id}"
    intent = client.post(
        base + "/action-intents",
        headers=headers,
        json={"action": "READ_CURRENT", "origin": "https://app.example.test", "sequence": 1},
    )
    assert intent.status_code == 201
    action_id = intent.json()["actionId"]
    url = base + f"/actions/{action_id}"
    if fault != "before-dispatch":
        assert (
            client.post(
                url + "/dispatch",
                headers=headers,
                json={"origin": "https://app.example.test"},
            ).status_code
            == 200
        )
    with workspace_connection(db, WS) as conn:
        fixture = conn.execute(
            "SELECT navigator_values FROM run_fixture_instance WHERE run_id=%s",
            (ref.run_id,),
        ).fetchone()
        assert fixture is not None
        fixture_text = next(iter(fixture["navigator_values"].values()))
        if fault == "revoked":
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=("
                "SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    source: dict[str, Any] = {
        "actionId": str(uuid.uuid4()) if fault == "identity" else action_id,
        "actionSequence": 1,
        "capturedAtUtc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phrase": "x" * 8193 if fault == "oversize" else f"Speech {fixture_text}",
    }
    if fault == "extra-field":
        source["eventType"] = "EFFECT_RECEIPT"
    body = {
        "producerSequence": 2 if fault == "gap" else 1,
        "sourceRecordDigest": "0" * 64 if fault == "digest" else digest(source),
        "sourceRecord": source,
    }
    result = client.post(url + "/observation", json=body, headers=headers)
    assert result.status_code == (200 if fault is None else 403)
    with workspace_connection(db, WS) as conn:
        events = conn.execute(
            "SELECT payload FROM canonical_event WHERE event_type='READER_OBSERVATION'"
        ).fetchall()
        assert len(events) == int(fault is None)
        if fault is None:
            payload = events[0]["payload"]
            assert fixture_text not in json.dumps(payload["sourceRecord"])
            assert "[REDACTED_FIXTURE]" in payload["sourceRecord"]["phrase"]
            assert payload["sourceRecordDigest"] == digest(payload["sourceRecord"])
            assert payload["submittedSourceRecordDigest"] == digest(source)
        assert run_store.load_run(conn, run_id=ref.run_id).state.outcome.value == "NOT_EVALUATED"
    if fault is None:
        # Same content is idempotent evidence admission, not permission to repeat an OS action.
        replay = client.post(url + "/observation", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == result.json()
        source["phrase"] = "conflicting evidence"
        body["sourceRecordDigest"] = digest(source)
        assert client.post(url + "/observation", json=body, headers=headers).status_code == 403
        assert (
            client.post(url + "/result", json={"status": "SUCCEEDED"}, headers=headers).status_code
            == 200
        )
        assert client.post(url + "/observation", json=body, headers=headers).status_code == 403


@pytest.fixture()
def owned_observer_database(db: str, backup_database_url: str) -> Iterator[str]:
    """A separate, exactly-owned app database. Never truncate the user's reference app."""
    from psycopg import sql

    from reference_app.db import SCHEMA

    def database(url: str, name: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))

    name = "accessforge_observer_" + uuid.uuid4().hex[:12]
    owner = urlsplit(db).username
    assert owner is not None
    with psycopg.connect(database(backup_database_url, "postgres"), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name),
                sql.Identifier(owner),
            )
        )
    try:
        url = database(db, name)
        with psycopg.connect(url) as conn:
            conn.execute(SCHEMA)
        yield url
    finally:
        with psycopg.connect(database(backup_database_url, "postgres"), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize("execution_body", ["action-policy"], indirect=True)
@pytest.mark.parametrize(
    "case",
    [
        "one",
        "zero",
        "missing-fixture",
        "revoked-during-read",
        "action-during-read",
        "wrong-template",
        "app-unreachable",
        "wrong-reference",
        "cli",
    ],
)
def test_independent_observer_worker(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    owned_observer_database: str,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    import secrets

    from accessforge_orchestrator.completion_observer import Refused, measure_once
    from accessforge_persistence.evidence.observer import ApplicationObserver
    from accessforge_persistence.fixtures import create_instance

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    secret = secrets.token_urlsafe(32)
    assert (
        client.post(
            _ticket_url(ticket).removesuffix("accept") + "session",
            json={"sessionSecret": secret},
            headers={"Authorization": f"Bearer {ticket.token}"},
        ).status_code
        == 201
    )
    headers = {"Authorization": f"Bearer {secret}"}
    base = f"/v1/workspaces/{WS}/supervisor-sessions/{ticket.ticket_id}"
    intended = client.post(
        base + "/action-intents",
        headers=headers,
        json={
            "action": "READ_CURRENT",
            "origin": "https://app.example.test",
            "sequence": 1,
        },
    )
    assert intended.status_code == 201
    action_url = base + "/actions/" + intended.json()["actionId"]
    assert (
        client.post(
            action_url + "/dispatch", headers=headers, json={"origin": "https://app.example.test"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            action_url + "/result", headers=headers, json={"status": "SUCCEEDED"}
        ).status_code
        == 200
    )
    with workspace_connection(db, WS) as conn:
        fixture = create_instance(
            conn,
            workspace_id=WS,
            run_id=ref.run_id,
            template_id="reference-service-request",
            template_digest=_digest("fixture"),
            navigator_values={"name": "Private Fixture Name"},
            observer_config={"effect": "CREATE_TEST_REQUEST"},
        )
    with psycopg.connect(owned_observer_database, row_factory=psycopg.rows.dict_row) as conn:
        if case != "missing-fixture":
            conn.execute(
                "INSERT INTO fixture_instance(nonce,template_digest,variant) "
                "VALUES(%s,%s,'accessible')",
                (
                    fixture.nonce,
                    _digest("wrong") if case == "wrong-template" else fixture.template_digest,
                ),
            )
        if case in {"one", "cli"}:
            conn.execute(
                "INSERT INTO service_request"
                "(id,fixture_nonce,full_name,email,category,description) "
                "VALUES(%s,%s,'Private Fixture Name','private@example.test','test','test')",
                (str(uuid.uuid4()), fixture.nonce),
            )
    if case in {"revoked-during-read", "action-during-read"}:
        original = ApplicationObserver.count_effects

        def revoked(observer: ApplicationObserver, **kwargs: Any) -> Any:
            measured = original(observer, **kwargs)
            if case == "revoked-during-read":
                with workspace_connection(db, WS) as conn:
                    conn.execute(
                        "UPDATE approval SET revoked_at=now() WHERE id=("
                        "SELECT authorization_id FROM run WHERE id=%s)",
                        (ref.run_id,),
                    )
            else:
                assert (
                    client.post(
                        base + "/action-intents",
                        headers=headers,
                        json={
                            "action": "READ_CURRENT",
                            "origin": "https://app.example.test",
                            "sequence": 2,
                        },
                    ).status_code
                    == 201
                )
            return measured

        monkeypatch.setattr(ApplicationObserver, "count_effects", revoked)
    source_id = str(uuid.uuid4())
    kwargs: dict[str, Any] = {
        "workspace_id": WS,
        "run_id": ref.run_id,
        "source_record_id": source_id,
        "credential_ref": "wrong" if case == "wrong-reference" else "observer-profile",
    }
    refused_cases = {"wrong-reference", "revoked-during-read", "action-during-read"}
    unknown_cases = {"missing-fixture", "wrong-template", "app-unreachable"}
    if case in refused_cases:
        with pytest.raises((Refused, runner_store.DispatchRefused)):
            measure_once(db, owned_observer_database, **kwargs)
    elif case == "cli":
        process = subprocess.run(  # noqa: S603 - owned observer entry point, no shell
            [
                sys.executable,
                "-m",
                "accessforge_orchestrator.completion_observer",
                "--workspace-id",
                WS,
                "--run-id",
                ref.run_id,
                "--source-record-id",
                source_id,
                "--observer-credential-ref",
                "observer-profile",
            ],
            env={
                "PATH": os.environ["PATH"],
                "ACCESSFORGE_DATABASE_URL": db,
                "ACCESSFORGE_OBSERVER_DATABASE_URL": owned_observer_database,
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert process.returncode == 0 and process.stdout.strip().endswith("KNOWN")
        assert db not in process.stdout + process.stderr
        assert owned_observer_database not in process.stdout + process.stderr
    else:
        application = (
            "postgresql://invalid@127.0.0.1:1/absent"
            if case == "app-unreachable"
            else owned_observer_database
        )
        receipt = measure_once(db, application, **kwargs)
        assert receipt.known == (case not in unknown_cases)
        # Replay uses retained bytes and never re-reads a now-unreachable application.
        replay = measure_once(db, "postgresql://invalid@127.0.0.1:1/absent", **kwargs)
        assert replay.replay and replay.event_id == receipt.event_id
    with workspace_connection(db, WS) as conn:
        events = conn.execute(
            "SELECT event_type,payload FROM canonical_event WHERE event_type='EFFECT_RECEIPT'",
        ).fetchall()
        assert len(events) == int(case not in refused_cases)
        if events:
            payload = events[0]["payload"]
            assert payload["serviceIdentity"] == "OBSERVER"
            source = payload["sourceRecord"]
            assert source["count"] == (
                None if case in unknown_cases else int(case in {"one", "cli"})
            )
            assert source["measurement"] == ("UNKNOWN" if case in unknown_cases else "KNOWN")
            assert payload["sourceRecordDigest"] == digest(source)
            assert fixture.nonce not in json.dumps(payload)
            assert "Private Fixture Name" not in json.dumps(payload)
        assert run_store.load_run(conn, run_id=ref.run_id).state.outcome.value == "NOT_EVALUATED"


@pytest.mark.parametrize("execution_body", ["stop-policy"], indirect=True)
@pytest.mark.parametrize(
    "case",
    [
        "success",
        "missing-observer",
        "tail",
        "no-stop",
        "unresolved-stop",
        "revoked",
        "rollback",
        "artifacts",
        "artifact-ack-loss",
        "artifact-journal-tail",
        "artifact-journal-id",
        "artifact-canonical",
        "artifact-deleted",
        "artifact-cli",
        "artifact-state",
        "artifact-stored-corrupt",
        "artifact-conflict",
        "observer-unknown",
    ],
)
def test_authenticated_execution_finish(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    owned_observer_database: str,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    tmp_path: Path,
) -> None:
    import secrets

    from accessforge_orchestrator.completion_observer import measure_once
    from accessforge_persistence.evidence import missing_required_artifacts
    from accessforge_persistence.fixtures import create_instance

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    closes = case in {"success", "observer-unknown"} or case.startswith("artifact")
    secret = secrets.token_urlsafe(32)
    assert (
        client.post(
            _ticket_url(ticket).removesuffix("accept") + "session",
            json={"sessionSecret": secret},
            headers={"Authorization": f"Bearer {ticket.token}"},
        ).status_code
        == 201
    )
    headers = {"Authorization": f"Bearer {secret}"}
    base = f"/v1/workspaces/{WS}/supervisor-sessions/{ticket.ticket_id}"
    with workspace_connection(db, WS) as conn:
        fixture = create_instance(
            conn,
            workspace_id=WS,
            run_id=ref.run_id,
            template_id="reference-service-request",
            template_digest=_digest("fixture"),
            navigator_values={"name": "Private Fixture"},
            observer_config={"effect": "CREATE_TEST_REQUEST"},
        )
    with psycopg.connect(owned_observer_database, row_factory=psycopg.rows.dict_row) as conn:
        conn.execute(
            "INSERT INTO fixture_instance(nonce,template_digest,variant) "
            "VALUES(%s,%s,'accessible')",
            (fixture.nonce, fixture.template_digest),
        )
    stop_id = ""
    for sequence, action in enumerate(
        ["READ_CURRENT"] if case == "no-stop" else ["READ_CURRENT", "STOP"],
        start=1,
    ):
        intent = client.post(
            base + "/action-intents",
            headers=headers,
            json={
                "action": action,
                "sequence": sequence,
                "origin": "https://app.example.test",
            },
        )
        assert intent.status_code == 201
        stop_id = intent.json()["actionId"]
        action_url = base + "/actions/" + stop_id
        assert (
            client.post(
                action_url + "/dispatch",
                headers=headers,
                json={"origin": "https://app.example.test"},
            ).status_code
            == 200
        )
        if action != "STOP":
            source = {
                "actionId": stop_id,
                "actionSequence": sequence,
                "capturedAtUtc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "phrase": "Synthetic reader evidence",
            }
            assert (
                client.post(
                    action_url + "/observation",
                    headers=headers,
                    json={
                        "producerSequence": 1,
                        "sourceRecordDigest": digest(source),
                        "sourceRecord": source,
                    },
                ).status_code
                == 200
            )
        if case != "unresolved-stop" or action != "STOP":
            assert (
                client.post(
                    action_url + "/result", headers=headers, json={"status": "SUCCEEDED"}
                ).status_code
                == 200
            )
    if case not in {"missing-observer", "no-stop", "unresolved-stop"}:
        receipt = measure_once(
            db,
            "postgresql://127.0.0.1:1/unavailable"
            if case == "observer-unknown"
            else owned_observer_database,
            workspace_id=WS,
            run_id=ref.run_id,
            credential_ref="observer-profile",
            source_record_id=str(uuid.uuid4()),
            final_sample=True,
        )
        assert receipt.known == (case != "observer-unknown")
        with workspace_connection(db, WS) as conn:
            observed = conn.execute(
                "SELECT payload FROM canonical_event WHERE event_id=%s", (receipt.event_id,)
            ).fetchone()
            assert observed is not None
            assertion = observed["payload"]["sourceRecord"]["assertionObservations"][0]
            assert assertion == {
                "assertionId": "completion.one-request",
                "kind": "TASK_COMPLETION",
                "condition": "UNKNOWN" if case == "observer-unknown" else "FALSE",
                "provenance": "OBSERVER_AUTHORED",
                **(
                    {"unknownReason": "measurement or matching frozen effect predicate unavailable"}
                    if case == "observer-unknown"
                    else {}
                ),
            }
    if case == "revoked":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=("
                "SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    if case == "rollback":
        original = run_store.apply_transition

        def fail_after_transition(*args: Any, **kwargs: Any) -> Any:
            original(*args, **kwargs)
            raise RuntimeError("synthetic closing transaction failure")

        monkeypatch.setattr(run_store, "apply_transition", fail_after_transition)
        with pytest.raises(RuntimeError, match="synthetic closing"):
            client.post(
                base + "/finish",
                headers=headers,
                json={"stopActionId": stop_id, "readerSequence": 1},
            )
    else:
        response = client.post(
            base + "/finish",
            headers=headers,
            json={
                "stopActionId": stop_id,
                "readerSequence": 0 if case == "tail" else 1,
            },
        )
        assert response.status_code == (200 if closes else 403)
        if closes:
            assert response.json()["status"] == "FINALIZING"
            assert response.json()["outcome"] == "NOT_EVALUATED"
            assert response.json()["missingArtifactCount"] == 5
            assert (
                client.post(
                    base + "/action-intents",
                    headers=headers,
                    json={
                        "action": "READ_CURRENT",
                        "sequence": 3,
                        "origin": "https://app.example.test",
                    },
                ).status_code
                == 403
            )
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert state.status.value == ("FINALIZING" if closes else "RUNNING")
        assert state.outcome.value == "NOT_EVALUATED" and state.cancel_requested_at is None
        lease = conn.execute(
            "SELECT released_at,stop_acknowledged_epoch,release_reason "
            "FROM desktop_lease WHERE id=%s",
            (ref.lease_id,),
        ).fetchone()
        assert lease is not None
        assert (lease["released_at"] is not None) == closes
        tails = conn.execute("SELECT closed_at_sequence FROM producer_stream").fetchall()
        if closes:
            assert len(tails) == 4 and all(t["closed_at_sequence"] is not None for t in tails)
            assert lease["release_reason"] == "STOP_ACKNOWLEDGED"
            assert lease["stop_acknowledged_epoch"] == ref.epoch
        assert (
            len(missing_required_artifacts(conn, run_id=ref.run_id, attempt_id=ref.attempt_id)) == 5
        )
        finished = conn.execute(
            "SELECT 1 FROM canonical_event WHERE event_type='RUN_FINISHED'"
        ).fetchall()
        assert len(finished) == int(closes)
    if case.startswith("artifact"):
        _retain_stopped_artifact_case(db, ref, ticket, monkeypatch, tmp_path, case)


def _retain_stopped_artifact_case(
    db: str,
    ref: DispatchReference,
    ticket: DispatchTicket,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    from accessforge_orchestrator.execution_artifacts import Refused, retain_bundle
    from accessforge_persistence import evidence
    from accessforge_persistence.evidence.session import requirements

    store = evidence.S3ArtifactStore(
        evidence.S3Settings(
            endpoint_url=os.environ["OBJECT_STORE_ENDPOINT"],
            access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
            secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
            bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        )
    )
    store.ensure_bucket()
    entries = []
    with workspace_connection(db, WS) as conn:
        actions = conn.execute(
            "SELECT * FROM runner_action WHERE run_id=%s ORDER BY action_sequence", (ref.run_id,)
        ).fetchall()
        for a in actions:
            intent = {
                "actionId": f"{ref.lease_id}:{ref.epoch}:{a['action_sequence']}",
                "serverActionId": str(a["id"]),
                "leaseId": ref.lease_id,
                "epoch": ref.epoch,
                "sequence": a["action_sequence"],
                "action": a["action"],
                "intentAtUtc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            entries.extend(
                [
                    intent,
                    {
                        **intent,
                        "result": a["result_status"],
                        "dispatchedAtMonotonic": float(a["action_sequence"]) + 0.5,
                    },
                ]
            )
    journal = b"".join(json.dumps(e).encode() + b"\n" for e in entries)
    if case == "artifact-journal-tail":
        journal += b'{"truncated":\n'
    if case == "artifact-journal-id":
        journal = journal.replace(str(actions[0]["id"]).encode(), str(uuid.uuid4()).encode())
    if case in {"artifact-canonical", "artifact-state"}:
        with workspace_connection(db, WS) as conn:
            if case == "artifact-canonical":
                conn.execute("UPDATE canonical_event SET payload='{}'::jsonb WHERE sequence=1")
            else:
                conn.execute("UPDATE run SET quarantined=true WHERE id=%s", (ref.run_id,))
    kwargs: dict[str, Any] = {"workspace_id": WS, "run_id": ref.run_id, "journal": journal}
    try:
        if case in {
            "artifact-journal-tail",
            "artifact-journal-id",
            "artifact-canonical",
            "artifact-state",
        }:
            with pytest.raises(Refused):
                retain_bundle(db, store, **kwargs)
            with workspace_connection(db, WS) as conn:
                assert conn.execute("SELECT 1 FROM evidence_artifact").fetchone() is None
            return
        if case == "artifact-ack-loss":
            original = store.put_create_only

            def lost_ack(*, key: str, payload: bytes, content_type: str) -> str:
                original(key=key, payload=payload, content_type=content_type)
                raise evidence.ObjectStoreUnavailable("synthetic lost write ACK")

            with monkeypatch.context() as patch:
                patch.setattr(store, "put_create_only", lost_ack)
                with pytest.raises(evidence.ObjectStoreUnavailable):
                    retain_bundle(db, store, **kwargs)
            with workspace_connection(db, WS) as conn:
                rows = conn.execute("SELECT state,object_key FROM evidence_artifact").fetchall()
                assert len(rows) == 5 and all(r["state"] == "QUARANTINED" for r in rows)
                assert sum(store.exists(key=r["object_key"]) for r in rows) == 1
        if case == "artifact-cli":
            spool = tmp_path / "journal.ndjson"
            spool.write_bytes(journal)
            spool.chmod(0o600)
            env = dict(
                os.environ,
                ACCESSFORGE_DATABASE_URL=db,
                ACCESSFORGE_EVIDENCE_ENDPOINT_URL=os.environ["OBJECT_STORE_ENDPOINT"],
                ACCESSFORGE_EVIDENCE_ACCESS_KEY=os.environ["OBJECT_STORE_ACCESS_KEY"],
                ACCESSFORGE_EVIDENCE_SECRET_KEY=os.environ["OBJECT_STORE_SECRET_KEY"],
                ACCESSFORGE_EVIDENCE_BUCKET=os.environ.get(
                    "OBJECT_STORE_BUCKET", "accessforge-evidence"
                ),
            )
            completed = subprocess.run(  # noqa: S603 - fixed local module and owned spool, no shell
                [
                    sys.executable,
                    "-m",
                    "accessforge_orchestrator.execution_artifacts",
                    "--workspace-id",
                    WS,
                    "--run-id",
                    ref.run_id,
                    "--journal",
                    str(spool),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert completed.returncode == 0, completed.stdout
            assert "count=5; outcome=NOT_EVALUATED" in completed.stdout
        ids = retain_bundle(db, store, **kwargs)
        assert len(ids) == 5 and retain_bundle(db, store, **kwargs) == ids
        with workspace_connection(db, WS) as conn:
            rows = conn.execute("SELECT * FROM evidence_artifact").fetchall()
            assert len(rows) == 5 and all(r["state"] == "PROMOTED" for r in rows)
            for artifact in rows:
                raw = store.get(key=artifact["object_key"])
                assert evidence.compute_digest(raw) == artifact["content_digest"]
                if artifact["kind"] == "RUNNER_JOURNAL":
                    assert raw == journal
            required = requirements(
                conn, {"id": ticket.ticket_id, "run_id": ref.run_id, "attempt_id": ref.attempt_id}
            )
            complete = evidence.assess_completeness(
                conn,
                store,
                run_id=ref.run_id,
                attempt_id=ref.attempt_id,
                required_producers=frozenset(
                    p for k, p in required.items() if k != "RUNNER_JOURNAL"
                ),
            )
            assert complete.complete and complete.artifacts_present and complete.producers_closed
            state = run_store.load_run(conn, run_id=ref.run_id).state
            assert state.status.value == "FINALIZING" and state.outcome.value == "NOT_EVALUATED"
        if case == "artifact-deleted":
            with workspace_connection(db, WS) as conn:
                evidence.delete_artifact_bytes(conn, store, artifact_id=ids[0], reason="owned test")
            with pytest.raises(Refused, match="deleted"):
                retain_bundle(db, store, **kwargs)
        if case == "artifact-stored-corrupt":
            key = rows[0]["object_key"]
            store.put(
                key=key, payload=b"synthetic corruption", content_type=rows[0]["content_type"]
            )
            with pytest.raises(Refused, match="stored bytes"):
                retain_bundle(db, store, **kwargs)
            assert store.get(key=key) == b"synthetic corruption"
        if case == "artifact-conflict":
            # Semantically identical JSON with different original spool bytes is not a replacement.
            with pytest.raises(Refused, match="conflicts"):
                retain_bundle(db, store, **{**kwargs, "journal": journal.replace(b": ", b":")})
    finally:
        with workspace_connection(db, WS) as conn:
            keys = conn.execute(
                "SELECT object_key FROM evidence_artifact WHERE run_id=%s", (ref.run_id,)
            ).fetchall()
        for item in keys:
            store.delete(key=item["object_key"])


@pytest.fixture(scope="module")
def native_receiver_command() -> Path:
    root = Path(__file__).resolve().parents[2]
    pnpm = shutil.which("pnpm")
    assert pnpm is not None, "pnpm is required for the native receiver integration proof"
    # A fresh checkout must test current TypeScript, never a stale ignored dist directory.
    for script in ("typecheck", "build"):
        subprocess.run(  # noqa: S603 - fixed workspace build command, no shell
            [pnpm, "--filter", "@accessforge/desktop-runner", script],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=60,
        )
    return root / "apps/desktop-runner/dist/receive-dispatch.js"


@pytest.fixture()
def native_receiver_api(settings: ApiSettings, db: str) -> Iterator[str]:
    # Real socket/server with test-only settings; no browser, reader, or authentication bypass.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), log_level="critical", access_log=False)
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                assert thread.is_alive(), "native receiver test API stopped during startup"
                time.sleep(0.01)
            else:
                pytest.fail("native receiver test API did not start")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "native receiver test API did not stop"


@pytest.mark.parametrize("execution_body", ["action-policy"], indirect=True)
@pytest.mark.parametrize("mode", ["intent", "execution", "ambiguity", "capture-unknown"])
def test_native_execution_session_real_http(
    db: str,
    native_receiver_command: Path,
    native_receiver_api: str,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    tmp_path: Path,
    mode: str,
) -> None:
    ref, ticket = manual_dispatch_reference, supervisor_ticket
    _seed_reader_fixture(db, ref)
    node = shutil.which("node")
    assert node is not None
    reference = {
        "workspaceId": ref.workspace_id,
        "runId": ref.run_id,
        "attemptId": ref.attempt_id,
        "runnerId": ref.runner_id,
        "leaseId": ref.lease_id,
        "epoch": ref.epoch,
    }
    claims = tmp_path.resolve() / "claims"
    claims.mkdir(mode=0o700)
    config = tmp_path.resolve() / "session.json"
    config.write_text(
        json.dumps(
            {
                "apiOrigin": native_receiver_api,
                "claimsDirectory": str(claims),
                "localReference": reference,
                "allowLoopbackHttp": True,
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    script = """
      const { NativeExecutionSession, readReceiverConfig } = await import(process.argv[1]);
      const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
      try {
        const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        const config = readReceiverConfig(process.argv[2]);
        const session = await NativeExecutionSession.open(config, input);
        if (process.argv[3] === 'intent') {
          const command = { action: 'READ_CURRENT', sequence: 1,
            origin: 'https://app.example.test' };
          const intent = await session.retainIntent(command);
          let refused = false;
          try { await session.retainIntent(command); } catch { refused = true; }
          process.stdout.write(JSON.stringify({ session, intent, duplicateRefused: refused }));
        } else {
          const base = process.argv[1];
          const { AuthenticatedRunner } = await import(new URL('./authenticated-runner.js', base));
          const { FileJournal } = await import(new URL('./journal.js', base));
          const { PREFLIGHT_CHECKS } = await import(process.argv[4]);
          let calls = 0;
          // Real HTTP/client/journal; explicitly synthetic physical probes and adapter.
          const runner = new AuthenticatedRunner({
            session, journal: new FileJournal(process.argv[2] + '.actions'),
            lease: { leaseId: input.reference.leaseId, epoch: input.reference.epoch,
              deadlineMonotonic: performance.now() + 10000,
              maxActions: 10, maxWallTimeSeconds: 30 },
            clock: { monotonic: () => performance.now(), utc: () => new Date().toISOString() },
            actionTimeoutMs: 1000,
            preflight: async () => ({ checks: Object.fromEntries(
              PREFLIGHT_CHECKS.map((key) => [key, { condition: 'TRUE' }])) }),
            observeOrigin: async () => 'https://app.example.test',
            authorizePhysicalAction: async () => {},
            adapter: { async perform(request, context) {
              calls++;
              if (process.argv[3] === 'ambiguity') throw new Error('synthetic adapter lost result');
              return { status: 'SUCCEEDED', observation: process.argv[3] === 'capture-unknown'
                ? { provenance: 'CAPTURE_UNKNOWN', reason: 'synthetic capture timeout' }
                : { phrase: 'Synthetic test speech', capturedAtUtc: context.capturedAtUtc(),
                    actionId: context.actionId, actionSequence: context.actionSequence,
                    domSnapshot: 'FORBIDDEN_DIAGNOSTIC',
                    fixtureAnswers: { secret: 'do not send' } } };
            } },
            recordObservation: async () => {},
          });
          const first = await runner.perform({ action: 'READ_CURRENT' });
          const second = await runner.perform({ action: 'READ_CURRENT' });
          process.stdout.write(JSON.stringify({ session, first, second, calls }));
        }
      } catch { process.stderr.write('native session failed'); process.exitCode = 78; }
    """
    result = subprocess.run(  # noqa: S603 - owned native client with secret on stdin, no shell
        [
            node,
            "--input-type=module",
            "--eval",
            script,
            native_receiver_command.with_name("dispatch-receiver.js").as_uri(),
            str(config),
            mode,
            (
                native_receiver_command.parents[3] / "packages/at-adapters/voiceover/dist/index.js"
            ).as_uri(),
        ],
        input=json.dumps(
            {
                "reference": reference,
                "ticket": {
                    "ticketId": ticket.ticket_id,
                    "token": ticket.token,
                    "expiresAt": ticket.expires_at,
                },
            }
        ),
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": os.environ["PATH"]},
    )
    assert result.returncode == 0 and result.stderr == ""
    assert ticket.token not in result.stdout
    output = json.loads(result.stdout)
    # JS private fields must not serialize even if somebody prints the entire session object.
    assert set(output["session"]) == {"receipt"}
    assert output["session"]["receipt"]["reference"] == reference
    if mode == "intent":
        assert output["intent"]["meaning"] == "ACTION_INTENT_RETAINED"
        assert output["duplicateRefused"] is True
    else:
        assert output["first"]["status"] == ("AMBIGUOUS" if mode == "ambiguity" else "SUCCEEDED")
        assert output["second"]["status"] == ("REFUSED" if mode == "ambiguity" else "SUCCEEDED")
        assert output["calls"] == (1 if mode == "ambiguity" else 2)
        journal = [
            json.loads(line) for line in Path(str(config) + ".actions").read_text().splitlines()
        ]
        assert len(journal) == (2 if mode == "ambiguity" else 4)
        assert all(entry["serverActionId"] for entry in journal)
    with workspace_connection(db, WS) as conn:
        action = conn.execute(
            "SELECT action,dispatched_at,result_at FROM runner_action WHERE run_id=%s",
            (ref.run_id,),
        ).fetchall()
        if mode == "intent":
            assert action == [{"action": "READ_CURRENT", "dispatched_at": None, "result_at": None}]
        else:
            assert len(action) == (1 if mode == "ambiguity" else 2)
            assert all(
                item["dispatched_at"] is not None and item["result_at"] is not None
                for item in action
            )
            state = run_store.load_run(conn, run_id=ref.run_id).state
            assert (state.status.value, state.outcome.value) == (
                ("INTERRUPTED", "INCONCLUSIVE")
                if mode == "ambiguity"
                else ("RUNNING", "NOT_EVALUATED")
            )
            events = conn.execute(
                "SELECT event_type,payload FROM canonical_event "
                "WHERE event_type='READER_OBSERVATION' ORDER BY sequence"
            ).fetchall()
            assert len(events) == (0 if mode == "ambiguity" else 2)
            for event in events:
                assert event["event_type"] == "READER_OBSERVATION"
                payload = event["payload"]
                assert payload["serviceIdentity"] == "SUPERVISOR"
                assert payload["sourceRecordDigest"] == digest(payload["sourceRecord"])
                assert "FORBIDDEN_DIAGNOSTIC" not in json.dumps(payload)
                assert "fixtureAnswers" not in json.dumps(payload)
                source = payload["sourceRecord"]
                if mode == "capture-unknown":
                    assert source["provenance"] == "CAPTURE_UNKNOWN" and "phrase" not in source
                else:
                    assert source["phrase"] == "Synthetic test speech"
        for statement in (
            "UPDATE supervisor_execution_session SET token_digest=repeat('a',64)",
            "UPDATE supervisor_execution_session SET expires_at=expires_at+interval '1 second'",
            "DELETE FROM supervisor_execution_session",
        ):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(statement)


@pytest.mark.parametrize("fault", [None, "local-identity", "existing-claim", "revoked-approval"])
def test_native_receiver_real_http_acceptance_and_restart(
    db: str,
    native_receiver_command: Path,
    native_receiver_api: str,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    tmp_path: Path,
    fault: str | None,
) -> None:
    """Cross-language bootstrap proof only: synthetic preflight, zero actual-reader actions."""
    ref, ticket = manual_dispatch_reference, supervisor_ticket
    node = shutil.which("node")
    assert node is not None, "Node is required for the native receiver integration proof"
    reference = {
        "workspaceId": ref.workspace_id,
        "runId": ref.run_id,
        "attemptId": ref.attempt_id,
        "runnerId": ref.runner_id,
        "leaseId": ref.lease_id,
        "epoch": ref.epoch,
    }
    local = dict(reference)
    if fault == "local-identity":
        local["attemptId"] = str(uuid.uuid4())
    claims = tmp_path.resolve() / "claims"
    claims.mkdir(mode=0o700)
    claim = claims / f"{ref.run_id}.json"
    if fault == "existing-claim":
        claim.write_text("{", encoding="utf-8")
    if fault == "revoked-approval":
        with workspace_connection(db, WS) as conn:
            conn.execute(
                "UPDATE approval SET revoked_at=now() WHERE id=("
                "SELECT authorization_id FROM run WHERE id=%s)",
                (ref.run_id,),
            )
    config_path = tmp_path.resolve() / "receiver.json"
    config_path.write_text(
        json.dumps(
            {
                "apiOrigin": native_receiver_api,
                "claimsDirectory": str(claims),
                "localReference": local,
                "allowLoopbackHttp": True,
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    payload = json.dumps(
        {
            "reference": reference,
            "ticket": {
                "ticketId": ticket.ticket_id,
                "token": ticket.token,
                "expiresAt": ticket.expires_at,
            },
        }
    )
    for invocation in range(2):
        result = subprocess.run(  # noqa: S603 - fixed native executable, secret on stdin only
            [node, str(native_receiver_command), str(config_path)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=15,
            # The native process must not inherit database/object-store/test credentials.
            env={"PATH": os.environ["PATH"]},
        )
        assert ticket.token not in result.stdout + result.stderr
        if fault is None and invocation == 0:
            assert result.returncode == 0
            assert json.loads(result.stdout) == {
                "ticketId": ticket.ticket_id,
                "reference": reference,
                "meaning": "DISPATCH_REFERENCE_ACCEPTED",
            }
        else:
            assert result.returncode == 78
            assert result.stdout == ""
    with workspace_connection(db, WS) as conn:
        stored = conn.execute(
            "SELECT accepted_at FROM supervisor_dispatch_ticket WHERE id=%s", (ticket.ticket_id,)
        ).fetchone()
        assert stored is not None
        assert (stored["accepted_at"] is not None) == (fault is None)
        audit = conn.execute(
            "SELECT detail FROM audit_event WHERE action='SUPERVISOR_DISPATCH_ACCEPTED'"
        ).fetchall()
        assert len(audit) == (1 if fault is None else 0)
        assert ticket.token not in json.dumps(audit)
        state = run_store.load_run(conn, run_id=ref.run_id).state
        assert (state.status.value, state.outcome.value) == ("RUNNING", "NOT_EVALUATED")
        assert conn.execute("SELECT count(*) AS n FROM runner_action").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM canonical_event").fetchone() == {"n": 0}
    if fault == "local-identity":
        assert not claim.exists()
    else:
        assert claim.exists() and ticket.token not in claim.read_text(encoding="utf-8")


def test_supervisor_ticket_is_machine_only_single_consumption_without_evidence(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
) -> None:
    import hashlib
    import json

    ticket = supervisor_ticket
    assert ticket.token not in repr(ticket)
    with workspace_connection(db, WS) as conn:
        stored = conn.execute(
            "SELECT * FROM supervisor_dispatch_ticket WHERE id=%s", (ticket.ticket_id,)
        ).fetchone()
        assert stored is not None and stored["accepted_at"] is None
        assert stored["token_digest"] == hashlib.sha256(ticket.token.encode()).hexdigest()
        assert ticket.token not in str(stored)
        assert (stored["expires_at"] - stored["created_at"]).total_seconds() <= 30
    # A logged-in OWNER is not machine authentication.
    assert client.post(_ticket_url(ticket), json={}).status_code == 401
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {ticket.token}", "Idempotency-Key": "not-a-restart"}
    accepted = client.post(_ticket_url(ticket), json={}, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.headers["cache-control"] == "no-store"
    assert accepted.json() == {
        "ticketId": ticket.ticket_id,
        "workspaceId": WS,
        "runId": manual_dispatch_reference.run_id,
        "attemptId": manual_dispatch_reference.attempt_id,
        "runnerId": manual_dispatch_reference.runner_id,
        "leaseId": manual_dispatch_reference.lease_id,
        "epoch": manual_dispatch_reference.epoch,
        "meaning": "DISPATCH_REFERENCE_ACCEPTED",
    }
    assert ticket.token not in accepted.text
    assert client.post(_ticket_url(ticket), json={}, headers=headers).status_code == 401
    assert client.get(f"/v1/workspaces/{WS}/runs", headers=headers).status_code == 401
    with workspace_connection(db, WS) as conn:
        state = run_store.load_run(conn, run_id=manual_dispatch_reference.run_id).state
        assert (state.status.value, state.outcome.value) == ("RUNNING", "NOT_EVALUATED")
        assert conn.execute("SELECT count(*) AS n FROM canonical_event").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM runner_action").fetchone() == {"n": 0}
        audit = conn.execute(
            "SELECT detail FROM audit_event WHERE action='SUPERVISOR_DISPATCH_ACCEPTED'"
        ).fetchall()
        assert len(audit) == 1 and ticket.token not in json.dumps(audit)


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-token",
        "unknown-ticket",
        "workspace",
        "expired",
        "revoked-ticket",
        "revoked-approval",
        "cancelled",
        "permission",
        "preflight",
        "body-claim",
        "duplicate-header",
        "human-session-token",
    ],
)
def test_receiver_rechecks_fresh_authority_without_consuming_on_refusal(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
    manual_dispatch_reference: DispatchReference,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    from accessforge_persistence import supervisor_dispatch

    ticket, ref = supervisor_ticket, manual_dispatch_reference
    url = _ticket_url(ticket)
    headers: Any = {"Authorization": f"Bearer {ticket.token}"}
    payload: dict[str, Any] = {}
    if fault == "wrong-token":
        headers = {"Authorization": "Bearer " + "x" * 43}
    elif fault == "unknown-ticket":
        url = _ticket_url(replace(ticket, ticket_id=str(uuid.uuid4())))
    elif fault == "workspace":
        url = _ticket_url(ticket, str(uuid.uuid4()))
    elif fault == "expired":

        class Later(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> Later:
                return cls.fromtimestamp((datetime.now(tz) + timedelta(seconds=60)).timestamp(), tz)

        monkeypatch.setattr(supervisor_dispatch, "datetime", Later)
    elif fault == "body-claim":
        payload = {"serviceIdentity": "SUPERVISOR", "runId": ref.run_id}
    elif fault == "duplicate-header":
        headers = [
            ("Authorization", f"Bearer {ticket.token}"),
            ("Authorization", f"Bearer {ticket.token}"),
        ]
    elif fault == "human-session-token":
        headers = {"Authorization": f"Bearer {client.cookies.get(SESSION_COOKIE)}"}
    else:
        with workspace_connection(db, WS) as conn:
            if fault == "revoked-ticket":
                conn.execute(
                    "UPDATE supervisor_dispatch_ticket SET revoked_at=now() WHERE id=%s",
                    (ticket.ticket_id,),
                )
            elif fault == "revoked-approval":
                conn.execute(
                    "UPDATE approval SET revoked_at=now() WHERE id="
                    "(SELECT authorization_id FROM run WHERE id=%s)",
                    (ref.run_id,),
                )
            elif fault == "cancelled":
                conn.execute(
                    "UPDATE run SET cancel_requested_at=now(),cancellation_revision=revision "
                    "WHERE id=%s",
                    (ref.run_id,),
                )
            elif fault == "permission":
                conn.execute(
                    "UPDATE workspace_membership SET role='VIEWER' WHERE user_id=%s", (OWNER,)
                )
            else:
                conn.execute(
                    "UPDATE runner_preflight SET successful=false WHERE runner_id=%s",
                    (ref.runner_id,),
                )
    response = client.post(url, json=payload, headers=headers)
    assert response.status_code == (400 if fault == "body-claim" else 401)
    assert ticket.token not in response.text
    with workspace_connection(db, WS) as conn:
        assert conn.execute(
            "SELECT accepted_at FROM supervisor_dispatch_ticket WHERE id=%s", (ticket.ticket_id,)
        ).fetchone() == {"accepted_at": None}


def test_concurrent_receivers_consume_one_ticket_once(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    ticket = supervisor_ticket

    def accept(_: int) -> int:
        barrier.wait(timeout=5)
        return client.post(
            _ticket_url(ticket), json={}, headers={"Authorization": f"Bearer {ticket.token}"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, range(2))) == [200, 401]
    with workspace_connection(db, WS) as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM audit_event WHERE action='SUPERVISOR_DISPATCH_ACCEPTED'"
        ).fetchone() == {"n": 1}


def test_dispatch_ticket_identity_and_consumption_cannot_be_rewritten(
    db: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
) -> None:
    ticket = supervisor_ticket
    assert (
        client.post(
            _ticket_url(ticket), json={}, headers={"Authorization": f"Bearer {ticket.token}"}
        ).status_code
        == 200
    )
    with workspace_connection(db, WS) as conn:
        for statement in (
            "UPDATE supervisor_dispatch_ticket SET token_digest=repeat('a',64)",
            "UPDATE supervisor_dispatch_ticket SET expires_at=expires_at+interval '1 hour'",
            "UPDATE supervisor_dispatch_ticket SET accepted_at=NULL",
            "UPDATE supervisor_dispatch_ticket SET epoch=epoch+1",
            "DELETE FROM supervisor_dispatch_ticket",
        ):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(statement)
        conn.execute("UPDATE supervisor_dispatch_ticket SET revoked_at=now()")
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE supervisor_dispatch_ticket SET revoked_at=NULL")


@pytest.mark.asyncio
async def test_accepted_http_handoff_with_lost_ack_is_revoked_not_replayed(
    db: str,
    client: TestClient,
    manual_dispatch_reference: DispatchReference,
) -> None:
    class LostResponse(SyntheticStartTransport):
        async def start(self, reference: DispatchReference, *, ticket: DispatchTicket) -> None:
            await super().start(reference, ticket=ticket)
            assert (
                client.post(
                    _ticket_url(ticket),
                    json={},
                    headers={"Authorization": f"Bearer {ticket.token}"},
                ).status_code
                == 200
            )
            raise ConnectionError("synthetic lost response after receiver commit")

    transport = LostResponse(db)
    with pytest.raises(HandoffUnknown):
        await ManualRunController(db, transport).dispatch(
            manual_dispatch_reference, expected_revision=1
        )
    assert transport.ticket is not None
    ticket = transport.ticket
    assert (
        client.post(
            _ticket_url(ticket), json={}, headers={"Authorization": f"Bearer {ticket.token}"}
        ).status_code
        == 401
    )
    with workspace_connection(db, WS) as conn:
        stored = conn.execute(
            "SELECT accepted_at,revoked_at FROM supervisor_dispatch_ticket WHERE id=%s",
            (ticket.ticket_id,),
        ).fetchone()
        assert stored is not None and stored["accepted_at"] and stored["revoked_at"]
        assert (
            run_store.load_run(conn, run_id=manual_dispatch_reference.run_id).state.status.value
            == "INTERRUPTED"
        )


def test_real_ticket_snapshot_restore_requires_irreversible_revocation(
    db: str,
    backup_database_url: str,
    client: TestClient,
    supervisor_ticket: DispatchTicket,
) -> None:
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    from accessforge_persistence import connect, restore, supervisor_dispatch

    ticket = supervisor_ticket
    snapshot = subprocess.run(  # noqa: S603 - fixed test command, owned database
        ["pg_dump", backup_database_url],  # noqa: S607 - provisioned PostgreSQL client
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert snapshot.returncode == 0, "owned dispatch snapshot failed"
    assert (
        client.post(
            _ticket_url(ticket), json={}, headers={"Authorization": f"Bearer {ticket.token}"}
        ).status_code
        == 200
    )
    name = "accessforge_ticket_restore_" + uuid.uuid4().hex[:12]

    def at_database(url: str, database: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))

    admin = at_database(backup_database_url, "postgres")
    target_admin, target_app = at_database(backup_database_url, name), at_database(db, name)
    with connect(admin) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - exact generated owned name
    try:
        loaded = subprocess.run(  # noqa: S603 - fixed command, owned target
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", target_admin],  # noqa: S607
            input=snapshot.stdout,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert loaded.returncode == 0, "owned dispatch restore failed"
        with workspace_connection(target_app, WS) as conn:
            assert conn.execute(
                "SELECT accepted_at,revoked_at FROM supervisor_dispatch_ticket WHERE id=%s",
                (ticket.ticket_id,),
            ).fetchone() == {"accepted_at": None, "revoked_at": None}
        with connect(target_admin) as conn:
            report = restore.reconcile(conn, operator="ticket-test", restore_id=str(uuid.uuid4()))
            assert report.supervisor_tickets_revoked == 1
        with workspace_connection(target_app, WS) as conn:
            row = conn.execute(
                "SELECT revoked_at FROM supervisor_dispatch_ticket WHERE id=%s", (ticket.ticket_id,)
            ).fetchone()
            assert row is not None and row["revoked_at"] is not None
            with pytest.raises(supervisor_dispatch.Refused):
                supervisor_dispatch.accept(
                    conn, workspace_id=WS, ticket_id=ticket.ticket_id, token=ticket.token
                )
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute("UPDATE supervisor_dispatch_ticket SET revoked_at=NULL")
    finally:
        with connect(admin) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')  # noqa: S608 - owned target only


@pytest.mark.asyncio
async def test_ticket_issue_failure_rolls_back_dispatch_and_secret(
    db: str,
    manual_dispatch_reference: DispatchReference,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from accessforge_persistence import supervisor_dispatch

    original = supervisor_dispatch.issue

    def fail_after_issue(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("synthetic ticket persistence failure")

    monkeypatch.setattr(supervisor_dispatch, "issue", fail_after_issue)
    transport = SyntheticStartTransport(db)
    with pytest.raises(RuntimeError, match="ticket persistence"):
        await ManualRunController(db, transport).dispatch(
            manual_dispatch_reference, expected_revision=1
        )
    assert transport.calls == 0 and transport.ticket is None
    with workspace_connection(db, WS) as conn:
        assert (
            run_store.load_run(conn, run_id=manual_dispatch_reference.run_id).state.status.value
            == "LEASED"
        )
        assert conn.execute("SELECT count(*) AS n FROM supervisor_dispatch_ticket").fetchone() == {
            "n": 0
        }
        assert (
            conn.execute(
                "SELECT 1 FROM audit_event WHERE action='MANUAL_DISPATCH_CLAIMED'"
            ).fetchone()
            is None
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
