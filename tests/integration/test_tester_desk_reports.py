"""Tester Desk client reporting and privacy (M06 · T-011/T-012)."""

from __future__ import annotations

import base64
from typing import Any

import pytest

pytestmark = pytest.mark.integration

from .conftest import AF21_BRIEF, Desk  # noqa: E402


def _obs(step: str, result: str = "PASSED") -> dict[str, Any]:
    return {
        "stepId": step,
        "result": result,
        "notes": f"notes for {step}",
        "source": "HUMAN_MANUAL",
        "observedAt": "2026-11-20T10:00:00Z",
    }


def _accepted(desk: Desk) -> dict[str, Any]:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF},
    )
    commission = created.json()
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    tester = desk.client_for(desk.tester_user)
    assignment = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    ).json()
    aid = assignment["assignmentId"]
    tester.put(
        desk.path(f"/tester/assignments/{aid}/draft"),
        json={
            "observations": [
                _obs("s1"),
                _obs("s2"),
                _obs("s3"),
                _obs("s4", "FAILED"),
                _obs("s5", "UNREACHABLE"),
            ],
            "baseRevision": 0,
        },
    )
    uploaded = tester.post(
        desk.path(f"/tester/assignments/{aid}/attachments"),
        json={
            "kind": "ACTION_TRACE",
            "contentType": "application/json",
            "filename": "focus-order-trace.json",
            "contentBase64": base64.b64encode(b'{"focus":["s4-trap"]}').decode(),
        },
    )
    submitted = tester.post(desk.path(f"/tester/assignments/{aid}/submit"), json={})
    desk.client_for(desk.reviewer).post(
        desk.path(f"/review/assignments/{aid}/decision"),
        json={
            "submissionDigest": submitted.json()["submissionHash"],
            "decision": "ACCEPTED",
            "justification": "complete",
        },
    )
    return {
        "assignmentId": aid,
        "commissionId": commission["commissionId"],
        "attachmentId": uploaded.json()["attachmentId"],
    }


def _grant(desk: Desk, client_id: str, user_id: str) -> None:
    agency = desk.client_for(desk.owner)
    granted = agency.post(desk.path(f"/clients/{client_id}/viewers"), json={"userId": user_id})
    assert granted.status_code == 201, granted.text


def test_t011_client_reads_redacted_observation_for_r18(desk: Desk) -> None:
    """T-011: the granted client sees the r18 report with the focus-trap finding, redacted."""
    ids = _accepted(desk)
    agency = desk.client_for(desk.owner)
    generated = agency.post(
        desk.path("/reports/generate"), json={"assignmentId": ids["assignmentId"]}
    )
    assert generated.status_code == 201, generated.text
    report = generated.json()
    assert report["releaseLabel"] == "r18"
    assert report["revision"] == 1
    assert report["audience"] == "CLIENT"

    _grant(desk, desk.client_id, desk.client_user)
    client = desk.client_for(desk.client_user)
    listed = client.get(desk.path("/client/reports"))
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1 and items[0]["reportId"] == report["reportId"]

    body = items[0]["body"]
    assert all(o.get("notes") is None for o in body["observations"])
    assert body["taskResult"] == "FAILED"
    results = {o["stepId"]: o["result"] for o in body["observations"]}
    assert results["s4"] == "FAILED" and results["s5"] == "UNREACHABLE"
    assert body["environment"]["assistiveTechnology"] == "VoiceOver"
    assert body["attachments"][0]["kind"] == "ACTION_TRACE"

    # Redaction: nothing the client sees carries tester identity, payout route or credentials.
    serialized = str(body)
    for forbidden in (
        desk.tester_id,
        desk.tester_user,
        "local-ledger:taylor",
        "desk-demo",
        "accessNote",
        "focus-order-trace.json",
    ):
        assert forbidden not in serialized


def test_t012_other_client_identity_cannot_fetch_attachment(desk: Desk) -> None:
    """T-012 + INV-06: a viewer of a different client gets 404 for this report's attachment."""
    ids = _accepted(desk)
    agency = desk.client_for(desk.owner)
    report = agency.post(
        desk.path("/reports/generate"), json={"assignmentId": ids["assignmentId"]}
    ).json()
    _grant(desk, desk.client_id, desk.client_user)

    # A second client with its own viewer grant.
    other_client = agency.post(desk.path("/clients"), json={"name": "Other Co"}).json()
    _grant(desk, other_client["clientId"], desk.other_client_user)
    other = desk.client_for(desk.other_client_user)

    report_path = desk.path(f"/client/reports/{report['reportId']}")
    assert other.get(report_path).status_code == 404
    attachment = other.get(f"{report_path}/attachments/{ids['attachmentId']}/content")
    assert attachment.status_code == 404
    # Their own grant works; the report list only shows their client's reports.
    assert other.get(desk.path("/client/reports")).json()["items"] == []

    # The granted client fetches the bytes and the digest verifies.
    client = desk.client_for(desk.client_user)
    fetched = client.get(f"{report_path}/attachments/{ids['attachmentId']}/content")
    assert fetched.status_code == 200
    assert base64.b64decode(fetched.json()["contentBase64"]) == b'{"focus":["s4-trap"]}'
    # The original filename is redacted from the client response.
    assert "filename" not in fetched.json()


def test_report_only_exists_for_accepted_work_and_retention_removes_bytes(
    desk: Desk,
) -> None:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-22", "brief": AF21_BRIEF},
    ).json()
    agency.post(desk.path(f"/commissions/{created['commissionId']}/publish"))
    tester = desk.client_for(desk.tester_user)
    aid = tester.post(
        desk.path(f"/tester/offers/{created['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    ).json()["assignmentId"]
    # Not submitted, not accepted: no report.
    refused = agency.post(desk.path("/reports/generate"), json={"assignmentId": aid})
    assert refused.status_code == 409

    ids = _accepted(desk)
    _grant(desk, desk.client_id, desk.client_user)
    agency.post(desk.path("/reports/generate"), json={"assignmentId": ids["assignmentId"]})

    # Retention removes working-set bytes for the terminal assignment; the report stands.
    purged = agency.post(desk.path("/retention/purge"), json={})
    assert purged.status_code == 200
    assert purged.json()["attachmentsPurged"] >= 1
    client = desk.client_for(desk.client_user)
    reports = client.get(desk.path("/client/reports")).json()["items"]
    assert len(reports) == 1
    gone = client.get(
        desk.path(
            f"/client/reports/{reports[0]['reportId']}/attachments/{ids['attachmentId']}/content"
        )
    )
    assert gone.status_code == 404


def test_author_can_resume_reporting_after_acceptance(desk: Desk) -> None:
    agency = desk.client_for(desk.owner)
    path = desk.path("/reports/ready")
    assert agency.get(path).json() == {"items": []}
    ids = _accepted(desk)
    expected = {
        "assignmentId": ids["assignmentId"],
        "commissionId": ids["commissionId"],
        "reference": "AF-21",
        "latestReportRevision": None,
    }
    ready = agency.get(path)
    assert ready.headers["cache-control"] == "no-store"
    assert ready.json()["items"] == [expected]
    assert desk.client_for(desk.reviewer).get(path).status_code == 403
    assert desk.client_for(desk.client_user).get(path).status_code == 404
    _grant(desk, desk.client_id, desk.client_user)
    for user in (desk.client_user, desk.tester_user):
        assert desk.client_for(user).get(path).status_code in (403, 404)
    for revision in (1, 2):
        generated = agency.post(
            desk.path("/reports/generate"), json={"assignmentId": ids["assignmentId"]}
        )
        assert generated.status_code == 201, generated.text
        assert agency.get(path).json()["items"] == [{**expected, "latestReportRevision": revision}]
