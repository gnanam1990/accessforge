"""Tester Desk commission and brief contracts (M01 · T-001)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration

from .conftest import AF21_BRIEF, Desk  # noqa: E402


def _create(
    desk: Desk, user: str, brief: dict[str, Any] | None = None, **over: object
) -> dict[str, Any]:
    client = desk.client_for(user)
    body = {
        "clientId": desk.client_id,
        "reference": "AF-21",
        "brief": brief or AF21_BRIEF,
        **over,
    }
    created = client.post(desk.path("/commissions"), json=body)
    assert created.status_code == 201, created.text
    result = created.json()
    assert isinstance(result, dict)
    return result


def test_t001_publish_r18_and_reopen_the_exact_brief_version(desk: Desk) -> None:
    """T-001: publish r18 with five steps and reopen the exact brief version."""
    agency = desk.client_for(desk.owner)
    commission = _create(desk, desk.owner)
    assert commission["status"] == "DRAFT"
    assert commission["currentBriefRevision"] is None

    published = agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "OPEN"
    assert published.json()["currentBriefRevision"] == 1

    reopened = agency.get(desk.path(f"/commissions/{commission['commissionId']}/briefs/1"))
    assert reopened.status_code == 200
    brief = reopened.json()
    assert brief["revision"] == 1
    assert brief["releaseLabel"] == "r18"
    assert len(brief["taskSteps"]) == 5
    assert brief["feeMinor"] == 2000
    assert brief["currency"] == "USD"
    assert brief["environment"] == {
        "browserName": "Safari",
        "browserVersion": "18",
        "assistiveTechnology": "VoiceOver",
        "inputMode": "KEYBOARD_ONLY",
    }
    assert brief["rubric"]["requiredStepIds"] == ["s1", "s2", "s3", "s4", "s5"]
    assert len(brief["briefHash"]) == 64
    # The hash commits to the terms: reopening is a verification, not a lookup.
    assert brief["accessNote"] == AF21_BRIEF["accessNote"]

    listed = agency.get(desk.path("/commissions"))
    ids = [c["commissionId"] for c in listed.json()["items"]]
    assert commission["commissionId"] in ids


def test_draft_reauthoring_then_reopen_keeps_revision_1(desk: Desk) -> None:
    """A draft can be re-authored; both pinned revisions remain readable unchanged."""
    agency = desk.client_for(desk.owner)
    commission = _create(desk, desk.owner)
    revised_brief = dict(AF21_BRIEF)
    revised_brief["feeMinor"] = 2500
    revised = agency.post(
        desk.path(f"/commissions/{commission['commissionId']}/revisions"),
        json={"brief": revised_brief},
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["revision"] == 2
    assert revised.json()["feeMinor"] == 2500

    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    assert (
        agency.get(desk.path(f"/commissions/{commission['commissionId']}")).json()[
            "currentBriefRevision"
        ]
        == 2
    )

    first = agency.get(desk.path(f"/commissions/{commission['commissionId']}/briefs/1"))
    assert first.status_code == 200 and first.json()["feeMinor"] == 2000
    assert first.json()["revision"] == 1
    # The two pinned versions carry different hashes over different terms.
    assert first.json()["briefHash"] != revised.json()["briefHash"]


def test_commission_authorization_boundaries(desk: Desk) -> None:
    """Only commission authors create; the roster/admin surface is owner-only."""
    reviewer = desk.client_for(desk.reviewer)
    created = reviewer.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-99", "brief": AF21_BRIEF},
    )
    assert created.status_code == 403
    assert created.json()["code"] == "PERMISSION_DENIED"

    outsider = desk.client_for(desk.outsider_user)
    assert (
        outsider.post(
            desk.path("/commissions"),
            json={"clientId": desk.client_id, "reference": "AF-98", "brief": AF21_BRIEF},
        ).status_code
        == 404
    )

    maintainer = desk.client_for(desk.maintainer)
    made = maintainer.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-97", "brief": AF21_BRIEF},
    )
    assert made.status_code == 201, made.text


def test_brief_validation_refuses_bad_terms(desk: Desk) -> None:
    agency = desk.client_for(desk.owner)
    bad = dict(AF21_BRIEF)
    bad["feeMinor"] = -1
    refused = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-96", "brief": bad},
    )
    assert refused.status_code == 400
    assert refused.json()["code"] == "INVALID_INPUT"

    bad_steps = dict(AF21_BRIEF)
    bad_steps["taskSteps"] = [{"stepId": "s1", "title": "only"}, {"stepId": "s1", "title": "dupe"}]
    refused2 = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-95", "brief": bad_steps},
    )
    assert refused2.status_code == 400

    bad_consent = dict(AF21_BRIEF)
    bad_consent["consentTermsVersion"] = 99
    refused3 = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-94", "brief": bad_consent},
    )
    assert refused3.status_code == 409
    assert "consent" in refused3.json()["detail"].lower()


def test_reopening_after_restart_returns_identical_terms(desk: Desk) -> None:
    """NFR-01: the pinned brief survives a fresh process reading the same row."""
    agency = desk.client_for(desk.owner)
    commission = _create(desk, desk.owner)
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    first = agency.get(desk.path(f"/commissions/{commission['commissionId']}/briefs/1")).json()

    # A second application instance -- the 'restart' boundary -- reads the same terms.
    fresh_client = desk.client_for(desk.owner)
    again = fresh_client.get(
        desk.path(f"/commissions/{commission['commissionId']}/briefs/1")
    ).json()
    assert again == first


@pytest.mark.parametrize(
    "rubric", [{"requiredStepIds": []}, {"requiredEvidenceKinds": ["SCREENSHOT"]}]
)
def test_refuses_unsatisfiable_or_empty_rubric(desk: Desk, rubric: dict[str, Any]) -> None:
    brief = {**AF21_BRIEF, "permittedEvidence": ["NOTE"], "rubric": rubric}
    agency = desk.client_for(desk.owner)
    response = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "invalid-rubric", "brief": brief},
    )
    assert response.status_code == 400
