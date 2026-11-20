"""Tester Desk integration fixtures.

A dedicated conftest rather than per-file copies: every tester-desk scenario needs the same
populated workspace -- an agency owner, a reviewer, enrolled testers and a client viewer -- and
duplicating that setup across eight files would hide the differences each test is actually about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from accessforge_api.app import create_app
from accessforge_api.auth import CSRF_HEADER, SESSION_COOKIE, issue_session
from accessforge_api.config import ApiSettings
from accessforge_persistence import migrate, unscoped_connection, workspace_connection
from accessforge_persistence import tester_desk as td

RELEASE_DIGEST = "a" * 64

AF21_BRIEF = {
    "releaseLabel": "r18",
    "releaseDigest": RELEASE_DIGEST,
    "targetUrl": "https://checkout.example.test/r18",
    "accessNote": "demo account tester@checkout.example.test / passphrase desk-demo",
    "taskSteps": [
        {"stepId": "s1", "title": "Open the release checkout URL"},
        {"stepId": "s2", "title": "Fill the contact form"},
        {"stepId": "s3", "title": "Fill the shipping form"},
        {"stepId": "s4", "title": "Advance past the shipping form"},
        {"stepId": "s5", "title": "Complete payment"},
    ],
    "environment": {
        "browserName": "Safari",
        "browserVersion": "18",
        "assistiveTechnology": "VoiceOver",
        "inputMode": "KEYBOARD_ONLY",
    },
    "rubric": {"requiredEvidenceKinds": ["ACTION_TRACE"]},
    "permittedEvidence": ["ACTION_TRACE", "SPEECH_TRANSCRIPT", "SCREENSHOT", "NOTE"],
    "feeMinor": 2000,
    "currency": "USD",
    "consentTermsVersion": 1,
}


@dataclass
class Desk:
    """A populated tester-desk workspace plus a per-user TestClient factory."""

    database_url: str
    workspace_id: str
    owner: str
    maintainer: str
    reviewer: str
    finance: str
    client_user: str
    other_client_user: str
    tester_user: str
    tester_id: str
    tester2_user: str
    tester2_id: str
    outsider_user: str
    client_id: str
    settings: ApiSettings = field(init=False)
    _sessions: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.settings = ApiSettings(
            database_url=self.database_url,
            evidence_endpoint_url="http://127.0.0.1:9000",
            evidence_bucket="unused",
            evidence_access_key="unused",
            evidence_secret_key="unused",
            environment="test",
        )

    def _session(self, user_id: str) -> tuple[str, str]:
        if user_id not in self._sessions:
            with workspace_connection(self.database_url, self.workspace_id) as conn:
                issued = issue_session(conn, user_id=user_id)
            self._sessions[user_id] = (issued.session_token, issued.csrf_token)
        return self._sessions[user_id]

    def client_for(self, user_id: str) -> TestClient:
        client = TestClient(create_app(self.settings))
        token, csrf = self._session(user_id)
        client.cookies.set(SESSION_COOKIE, token)
        # Mutating calls need this header on every request; the session binds it.
        client.headers[CSRF_HEADER] = csrf
        return client

    def path(self, suffix: str) -> str:
        return f"/v1/workspaces/{self.workspace_id}/tester-desk{suffix}"


@pytest.fixture
def desk(test_database_url: str) -> Desk:
    migrate(test_database_url)
    workspace, owner, maintainer, reviewer, finance = (str(uuid4()) for _ in range(5))
    client_user, other_client_user = str(uuid4()), str(uuid4())
    tester_user, tester2_user, outsider = (str(uuid4()) for _ in range(3))
    with unscoped_connection(test_database_url) as conn:
        conn.execute(
            "INSERT INTO workspace(id,name) VALUES (%s,'Tester Desk test')", (workspace,)
        )
        # Emails are globally unique in app_user, so they carry the run's suffix.
        for user, email in (
            (owner, f"lead+{workspace[:8]}@agency.example.test"),
            (maintainer, f"producer+{workspace[:8]}@agency.example.test"),
            (reviewer, f"reviewer+{workspace[:8]}@agency.example.test"),
            (finance, f"accounting+{workspace[:8]}@agency.example.test"),
            (client_user, f"viewer+{workspace[:8]}@client.example.test"),
            (other_client_user, f"other+{workspace[:8]}@otherclient.example.test"),
            (tester_user, f"taylor+{workspace[:8]}@testers.example.test"),
            (tester2_user, f"jordan+{workspace[:8]}@testers.example.test"),
            (outsider, f"outsider+{workspace[:8]}@elsewhere.example.test"),
        ):
            conn.execute("INSERT INTO app_user(id,email) VALUES (%s,%s)", (user, email))
    with workspace_connection(test_database_url, workspace) as conn:
        for user, role in (
            (owner, "OWNER"),
            (maintainer, "MAINTAINER"),
            (reviewer, "REVIEWER"),
            (finance, "OWNER"),
        ):
            conn.execute(
                "INSERT INTO workspace_membership(workspace_id,user_id,role) VALUES (%s,%s,%s)",
                (workspace, user, role),
            )
        td.publish_consent_terms(
            conn,
            workspace_id=workspace,
            body=(
                "You will perform the stated journey yourself with the declared assistive "
                "technology. You choose which permitted artifacts to attach. You may withdraw "
                "consent at any time; retained evidence follows the workspace data policy."
            ),
            actor_id=owner,
        )
        client = td.create_client(conn, workspace_id=workspace, name="Client Co")
        tester = td.enroll_tester(
            conn,
            workspace_id=workspace,
            actor_id=owner,
            user_id=tester_user,
            display_name="Taylor Tester",
            capabilities={
                "browserName": "Safari",
                "browserVersion": "18",
                "assistiveTechnology": "VoiceOver",
            },
            payout_reference="local-ledger:taylor",
        )
        tester2 = td.enroll_tester(
            conn,
            workspace_id=workspace,
            actor_id=owner,
            user_id=tester2_user,
            display_name="Jordan Tester",
            capabilities={
                "browserName": "Safari",
                "browserVersion": "18",
                "assistiveTechnology": "VoiceOver",
            },
            payout_reference="local-ledger:jordan",
        )
    return Desk(
        database_url=test_database_url,
        workspace_id=workspace,
        owner=owner,
        maintainer=maintainer,
        reviewer=reviewer,
        finance=finance,
        client_user=client_user,
        other_client_user=other_client_user,
        tester_user=tester_user,
        tester_id=tester["testerId"],
        tester2_user=tester2_user,
        tester2_id=tester2["testerId"],
        outsider_user=outsider,
        client_id=client["clientId"],
    )
