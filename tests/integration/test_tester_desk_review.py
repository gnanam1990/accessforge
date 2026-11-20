"""Tester Desk submission sealing and digest-bound review (M04 · T-007/T-008)."""

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


def _claimed_assignment(desk: Desk) -> dict[str, Any]:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF},
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    tester = desk.client_for(desk.tester_user)
    claimed = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assert claimed.status_code == 201, claimed.text
    result = claimed.json()
    assert isinstance(result, dict)
    return result


def _complete_failed_journey(desk: Desk, assignment_id: str) -> None:
    """The reference failure: step four traps focus, step five is unreachable."""
    tester = desk.client_for(desk.tester_user)
    observations = [
        _obs("s1"),
        _obs("s2"),
        _obs("s3"),
        _obs("s4", "FAILED"),
        _obs("s5", "UNREACHABLE"),
    ]
    saved = tester.put(
        desk.path(f"/tester/assignments/{assignment_id}/draft"),
        json={"observations": observations, "baseRevision": 0},
    )
    assert saved.status_code == 200, saved.text
    trace = b'{"events":[{"step":"s4","action":"focus","result":"trap"}]}'
    uploaded = tester.post(
        desk.path(f"/tester/assignments/{assignment_id}/attachments"),
        json={
            "kind": "ACTION_TRACE",
            "contentType": "application/json",
            "filename": "trace.json",
            "contentBase64": base64.b64encode(trace).decode(),
        },
    )
    assert uploaded.status_code == 201, uploaded.text


def test_t007_failed_task_earns_evidence_acceptance(desk: Desk) -> None:
    """T-007 + INV-04: s4 focus trap, s5 unreachable → task FAILED, evidence ACCEPTED."""
    assignment = _claimed_assignment(desk)
    _complete_failed_journey(desk, assignment["assignmentId"])
    tester = desk.client_for(desk.tester_user)
    reviewer = desk.client_for(desk.reviewer)

    submitted = tester.post(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={}
    )
    assert submitted.status_code == 200, submitted.text
    submission = submitted.json()
    assert submission["taskResult"] == "FAILED"
    assert submission["revision"] == 1
    assert len(submission["attachmentManifest"]) == 1

    # The reviewer sees the work in the queue and can open the sealed evidence.
    queue = reviewer.get(desk.path("/review/queue"))
    assert queue.status_code == 200
    assert queue.json()["items"][0]["assignmentId"] == assignment["assignmentId"]

    detail = reviewer.get(desk.path(f"/review/assignments/{assignment['assignmentId']}"))
    assert detail.status_code == 200
    assert detail.json()["submissions"][0]["submissionHash"] == submission["submissionHash"]
    # The access note is not part of the reviewer's view.
    assert "accessNote" not in str(detail.json()["brief"])

    decision = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": submission["submissionHash"],
            "decision": "ACCEPTED",
            "justification": "complete evidence of the focus trap at step four",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["decision"] == "ACCEPTED"

    # Assignment completed; commission accepted; evidence still replayable after the fact.
    mine = tester.get(desk.path(f"/tester/assignments/{assignment['assignmentId']}"))
    assert mine.json()["status"] == "COMPLETED"


def test_t008_obsolete_submission_digest_conflicts(desk: Desk) -> None:
    """T-008: approval bound to a superseded submission digest is a conflict."""
    assignment = _claimed_assignment(desk)
    _complete_failed_journey(desk, assignment["assignmentId"])
    tester = desk.client_for(desk.tester_user)
    reviewer = desk.client_for(desk.reviewer)

    first = tester.post(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={}
    )
    assert first.status_code == 200
    first_digest = first.json()["submissionHash"]

    # Reviewer asks for a revision; tester seals a new revision with more evidence.
    request_revision = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": first_digest,
            "decision": "NEEDS_REVISION",
            "justification": "add a note describing the observed focus order",
        },
    )
    assert request_revision.status_code == 200

    more = tester.put(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft"),
        json={
            "observations": [
                _obs("s1"),
                _obs("s2"),
                _obs("s3"),
                _obs("s4", "FAILED"),
                _obs("s5", "UNREACHABLE"),
            ],
            "baseRevision": 1,
        },
    )
    assert more.status_code == 200
    second = tester.post(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={}
    )
    assert second.status_code == 200
    assert second.json()["revision"] == 2

    # The reviewer still holds revision one's digest; approving it must conflict.
    stale = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": first_digest,
            "decision": "ACCEPTED",
            "justification": "approving the earlier evidence set",
        },
    )
    assert stale.status_code == 409

    fresh = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": second.json()["submissionHash"],
            "decision": "ACCEPTED",
            "justification": "revision two evidence is complete",
        },
    )
    assert fresh.status_code == 200
    assert fresh.json()["decision"] == "ACCEPTED"


def test_incomplete_submission_is_refused_with_the_gaps(desk: Desk) -> None:
    assignment = _claimed_assignment(desk)
    tester = desk.client_for(desk.tester_user)
    tester.put(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft"),
        json={"observations": [_obs("s1")], "baseRevision": 0},
    )
    refused = tester.post(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={}
    )
    assert refused.status_code == 409
    detail = refused.json()
    text = detail.get("detail") or detail.get("title") or str(detail)
    assert "s4" in text or "ACTION_TRACE" in text


def test_revision_request_reopens_capture_and_decision_requires_reason(
    desk: Desk,
) -> None:
    assignment = _claimed_assignment(desk)
    _complete_failed_journey(desk, assignment["assignmentId"])
    tester = desk.client_for(desk.tester_user)
    reviewer = desk.client_for(desk.reviewer)
    submit = tester.post(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={}
    )
    digest = submit.json()["submissionHash"]

    # Justification is mandatory.
    no_reason = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={"submissionDigest": digest, "decision": "NEEDS_REVISION", "justification": ""},
    )
    assert no_reason.status_code == 409

    # A second decision on an already-decided submission conflicts.
    asked = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": digest,
            "decision": "NEEDS_REVISION",
            "justification": "needs a note",
        },
    )
    assert asked.status_code == 200
    again = reviewer.post(
        desk.path(f"/review/assignments/{assignment['assignmentId']}/decision"),
        json={
            "submissionDigest": digest,
            "decision": "ACCEPTED",
            "justification": "changed my mind",
        },
    )
    assert again.status_code == 409

    # Capture reopens for the same tester: the draft may be amended.
    reopened = tester.put(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft"),
        json={"observations": [_obs("s1")], "baseRevision": 1},
    )
    assert reopened.status_code == 200


def test_review_requires_the_review_role(desk: Desk) -> None:
    """INV-03: non-reviewers never reach sealed evidence or the decision endpoint."""
    assignment = _claimed_assignment(desk)
    _complete_failed_journey(desk, assignment["assignmentId"])
    tester = desk.client_for(desk.tester_user)
    tester.post(desk.path(f"/tester/assignments/{assignment['assignmentId']}/submit"), json={})
    # The tester who owns the work is not a reviewer.
    own_queue = tester.get(desk.path("/review/queue"))
    assert own_queue.status_code in (403, 404)
    # An unenrolled outsider cannot review either.
    outsider = desk.client_for(desk.outsider_user)
    assert outsider.get(desk.path("/review/queue")).status_code in (403, 404)
    # Reviewer can read an attachment's bytes; a non-reviewer cannot.
    attachment_id = (
        desk.client_for(desk.reviewer)
        .get(desk.path(f"/review/assignments/{assignment['assignmentId']}"))
        .json()["submissions"][0]["attachmentManifest"][0]["attachmentId"]
    )
    denied = tester.get(
        desk.path(
            f"/review/assignments/{assignment['assignmentId']}/attachments/{attachment_id}/content"
        )
    )
    assert denied.status_code in (403, 404)
