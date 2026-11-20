"""Tester Desk retest linking and release comparison (M07 · T-013/T-014)."""

from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.integration

from conftest import AF21_BRIEF, Desk  # noqa: E402

R19_BRIEF = dict(AF21_BRIEF, releaseLabel="r19", releaseDigest="b" * 64)


def _obs(step: str, result: str = "PASSED") -> dict:
    return {
        "stepId": step,
        "result": result,
        "source": "HUMAN_MANUAL",
        "observedAt": "2026-11-20T10:00:00Z",
    }


def _publish(desk: Desk, reference: str, brief: dict, **extra) -> dict:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": reference, "brief": brief, **extra},
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    return commission


def _accepted_af21(desk: Desk) -> dict:
    commission = _publish(desk, "AF-21", AF21_BRIEF, defectKey="focus-trap-s4")
    tester = desk.client_for(desk.tester_user)
    aid = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    ).json()["assignmentId"]
    tester.put(
        desk.path(f"/tester/assignments/{aid}/draft"),
        json={
            "observations": [
                _obs("s1"), _obs("s2"), _obs("s3"),
                _obs("s4", "FAILED"), _obs("s5", "UNREACHABLE"),
            ],
            "baseRevision": 0,
        },
    )
    tester.post(
        desk.path(f"/tester/assignments/{aid}/attachments"),
        json={
            "kind": "ACTION_TRACE",
            "contentType": "application/json",
            "filename": "trace.json",
            "contentBase64": base64.b64encode(b'{"e":1}').decode(),
        },
    )
    submitted = tester.post(
        desk.path(f"/tester/assignments/{aid}/submit"), json={}
    )
    desk.client_for(desk.reviewer).post(
        desk.path(f"/review/assignments/{aid}/decision"),
        json={
            "submissionDigest": submitted.json()["submissionHash"],
            "decision": "ACCEPTED",
            "justification": "complete",
        },
    )
    return commission


def test_t013_r19_retest_is_untested_next_to_r18_failure(desk: Desk) -> None:
    """T-013 + INV-07: the linked r19 commission is pending; r18's failure is not inherited."""
    base = _accepted_af21(desk)
    retest = _publish(
        desk,
        "AF-22",
        R19_BRIEF,
        defectKey="focus-trap-s4",
        predecessorCommissionId=base["commissionId"],
    )
    agency = desk.client_for(desk.owner)

    # The retest lists under the defect and sits untested.
    linked = agency.get(desk.path("/defects/focus-trap-s4/commissions"))
    refs = {c["reference"]: c["status"] for c in linked.json()["items"]}
    assert refs == {"AF-21": "ACCEPTED", "AF-22": "OPEN"}

    comparison = agency.get(
        desk.path(f"/commissions/{base['commissionId']}/compare"),
        params={"against": retest["commissionId"]},
    )
    assert comparison.status_code == 200, comparison.text
    body = comparison.json()
    assert body["link"] == "SAME_DEFECT_RETEST"
    assert body["base"]["taskResult"] == "FAILED"
    assert body["base"]["deliverableDecision"] == "ACCEPTED"
    assert body["base"]["releaseLabel"] == "r18"
    # The candidate carries no inherited verdict.
    assert body["candidate"]["releaseLabel"] == "r19"
    assert body["candidate"]["taskResult"] is None
    assert body["candidate"]["deliverableDecision"] is None
    assert not any("same release" in c for c in body["caveats"])


def test_t014_different_assistive_technology_surfaces_a_caveat(desk: Desk) -> None:
    """T-014: comparing across assistive technologies shows the comparison caveat."""
    base = _accepted_af21(desk)
    nvda_brief = dict(
        R19_BRIEF,
        environment={
            "browserName": "Firefox",
            "browserVersion": "132",
            "assistiveTechnology": "NVDA",
            "inputMode": "KEYBOARD_ONLY",
        },
    )
    other = _publish(desk, "AF-23", nvda_brief, defectKey="focus-trap-s4")
    agency = desk.client_for(desk.owner)
    comparison = agency.get(
        desk.path(f"/commissions/{base['commissionId']}/compare"),
        params={"against": other["commissionId"]},
    )
    body = comparison.json()
    joined = " ".join(body["caveats"])
    assert "assistiveTechnology differs" in joined
    assert "browserName differs" in joined
    assert "VoiceOver" in joined and "NVDA" in joined


def test_comparison_between_unlinked_commissions_is_marked_informational(
    desk: Desk,
) -> None:
    base = _accepted_af21(desk)
    unrelated = _publish(desk, "AF-30", R19_BRIEF)  # no defect link
    agency = desk.client_for(desk.owner)
    body = agency.get(
        desk.path(f"/commissions/{base['commissionId']}/compare"),
        params={"against": unrelated["commissionId"]},
    ).json()
    assert body["link"] == "UNRELATED"
    assert any("informational only" in c for c in body["caveats"])

    # Same-release rerun gets its own caveat.
    same_release = _publish(desk, "AF-31", dict(AF21_BRIEF), defectKey="focus-trap-s4")
    body2 = agency.get(
        desk.path(f"/commissions/{base['commissionId']}/compare"),
        params={"against": same_release["commissionId"]},
    ).json()
    assert any("same release" in c for c in body2["caveats"])
