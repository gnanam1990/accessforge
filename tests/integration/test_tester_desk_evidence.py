"""Tester Desk evidence capture: drafts, attachments and scoping (M03 · T-005/T-006)."""

from __future__ import annotations

import base64
import hashlib

import pytest

pytestmark = pytest.mark.integration

from conftest import AF21_BRIEF, Desk  # noqa: E402


def _obs(step: str, result: str = "PASSED") -> dict:
    return {
        "stepId": step,
        "result": result,
        "notes": f"notes for {step}",
        "source": "HUMAN_MANUAL",
        "observedAt": "2026-11-20T10:00:00Z",
    }


def _claimed_assignment(desk: Desk) -> dict:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF},
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    published = agency.post(
        desk.path(f"/commissions/{commission['commissionId']}/publish")
    )
    assert published.status_code == 200, published.text
    tester = desk.client_for(desk.tester_user)
    claimed = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assert claimed.status_code == 201, claimed.text
    return claimed.json()


def test_t005_reload_partial_draft_recovers_three_observations(desk: Desk) -> None:
    """T-005: save three step observations, reload the draft, recover all three."""
    assignment = _claimed_assignment(desk)
    tester = desk.client_for(desk.tester_user)
    draft_path = desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft")

    # Fresh assignment: no draft yet.
    empty = tester.get(draft_path)
    assert empty.status_code == 200
    assert empty.json() == {
        "assignmentId": assignment["assignmentId"],
        "draftRevision": 0,
        "observations": [],
        "updatedAt": None,
    }

    # Autosave a partially completed draft: three of five steps observed.
    saved = tester.put(
        draft_path,
        json={"observations": [_obs("s1"), _obs("s2"), _obs("s3")], "baseRevision": 0},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draftRevision"] == 1

    # "Reload": a brand-new TestClient (fresh in-memory state) reads the draft back.
    reloaded = desk.client_for(desk.tester_user).get(draft_path)
    assert reloaded.status_code == 200
    body = reloaded.json()
    assert body["draftRevision"] == 1
    assert [o["stepId"] for o in body["observations"]] == ["s1", "s2", "s3"]
    assert all(o["source"] == "HUMAN_MANUAL" for o in body["observations"])

    # A stale writer from an older base is refused instead of overwriting.
    stale = tester.put(
        draft_path,
        json={"observations": [_obs("s4")], "baseRevision": 0},
    )
    assert stale.status_code == 409

    # Autosave continues on the current base.
    saved2 = tester.put(
        draft_path,
        json={
            "observations": [_obs("s1"), _obs("s2"), _obs("s3"), _obs("s4", "FAILED")],
            "baseRevision": 1,
        },
    )
    assert saved2.status_code == 200, saved2.text
    assert saved2.json()["draftRevision"] == 2


def test_t006_disallowed_recording_creates_no_downloadable_artifact(desk: Desk) -> None:
    """T-006: a disallowed recording type is refused and leaves no artifact."""
    assignment = _claimed_assignment(desk)
    tester = desk.client_for(desk.tester_user)
    base = desk.path(f"/tester/assignments/{assignment['assignmentId']}/attachments")
    payload = {
        "kind": "SCREEN_RECORDING",
        "contentType": "video/mp4",
        "filename": "run.mp4",
        "contentBase64": base64.b64encode(b"pretend-video").decode(),
    }
    refused = tester.post(base, json=payload)
    assert refused.status_code == 400

    # A permitted kind with a content type it does not allow is refused the same way.
    refused2 = tester.post(
        base,
        json={
            "kind": "SCREENSHOT",
            "contentType": "text/html",
            "filename": "page.html",
            "contentBase64": base64.b64encode(b"<html>capture</html>").decode(),
        },
    )
    assert refused2.status_code == 400

    # An evidence kind the brief does not list in permittedEvidence.
    refused3 = tester.post(
        base,
        json={
            "kind": "SPEECH_TRANSCRIPT",
            "contentType": "video/mp4",
            "filename": "a.mp4",
            "contentBase64": base64.b64encode(b"audio-ish").decode(),
        },
    )
    assert refused3.status_code == 400

    # No downloadable artifact exists.
    listed = tester.get(base)
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_permitted_attachment_round_trips_with_verified_digest(desk: Desk) -> None:
    assignment = _claimed_assignment(desk)
    tester = desk.client_for(desk.tester_user)
    base = desk.path(f"/tester/assignments/{assignment['assignmentId']}/attachments")
    content = b'{"events":[{"step":"s4","action":"focus-trap"}]}'
    uploaded = tester.post(
        base,
        json={
            "kind": "ACTION_TRACE",
            "contentType": "application/json",
            "filename": "trace.json",
            "contentBase64": base64.b64encode(content).decode(),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    assert attachment["sha256"] == hashlib.sha256(content).hexdigest()
    assert attachment["byteSize"] == len(content)

    fetched = tester.get(f"{base}/{attachment['attachmentId']}/content")
    assert fetched.status_code == 200
    assert base64.b64decode(fetched.json()["contentBase64"]) == content

    # Progress reflects the rubric: no observations yet, so the draft blocks submit.
    progress = tester.get(
        desk.path(f"/tester/assignments/{assignment['assignmentId']}/progress")
    )
    assert progress.status_code == 200
    body = progress.json()
    assert body["submittable"] is False
    assert any("s4" in gap or "step" in gap for gap in body["missing"])


def test_evidence_is_scoped_to_the_owning_tester(desk: Desk) -> None:
    assignment = _claimed_assignment(desk)
    owner = desk.client_for(desk.tester_user)
    other = desk.client_for(desk.tester2_user)
    outsider = desk.client_for(desk.outsider_user)
    draft_path = desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft")
    attach_path = desk.path(f"/tester/assignments/{assignment['assignmentId']}/attachments")

    owner.put(draft_path, json={"observations": [_obs("s1")], "baseRevision": 0})

    # Another enrolled tester never claimed this assignment: no visibility at all.
    for resp in (other.get(draft_path), other.get(attach_path)):
        assert resp.status_code == 404
    refused_write = other.put(
        draft_path, json={"observations": [_obs("s1")], "baseRevision": 0}
    )
    assert refused_write.status_code == 404

    # An unenrolled user is refused at the tester boundary entirely.
    assert outsider.get(draft_path).status_code in (403, 404)


def test_observations_reject_automation_source(desk: Desk) -> None:
    """INV-08: no source value exists that lets automation populate a human result."""
    assignment = _claimed_assignment(desk)
    tester = desk.client_for(desk.tester_user)
    draft_path = desk.path(f"/tester/assignments/{assignment['assignmentId']}/draft")
    bad = dict(_obs("s1"))
    bad["source"] = "TOOL_GENERATED"
    refused = tester.put(
        draft_path, json={"observations": [bad], "baseRevision": 0}
    )
    assert refused.status_code == 409
    assert tester.get(draft_path).json()["observations"] == []
