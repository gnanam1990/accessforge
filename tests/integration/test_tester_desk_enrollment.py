"""Tester Desk eligibility, consent and exclusive claims (M02 · T-002/T-003/T-004)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2
import pytest
from psycopg.errors import IntegrityError

pytestmark = pytest.mark.integration

from accessforge_persistence import workspace_connection  # noqa: E402

from .conftest import AF21_BRIEF, Desk  # noqa: E402


def _published_af21(desk: Desk) -> dict[str, Any]:
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF},
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    published = agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    assert published.status_code == 200, published.text
    result = published.json()
    assert isinstance(result, dict)
    return result


def _claim(desk: Desk, user: str, commission_id: str) -> httpx2.Response:
    tester = desk.client_for(user)
    return tester.post(
        desk.path(f"/tester/offers/{commission_id}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )


def test_t003_consenting_matching_tester_receives_the_assignment(desk: Desk) -> None:
    """T-003: a consenting tester matching Safari + VoiceOver receives the assignment."""
    commission = _published_af21(desk)
    tester = desk.client_for(desk.tester_user)

    offers = tester.get(desk.path("/tester/offers"))
    assert offers.status_code == 200
    ids = [o["commissionId"] for o in offers.json()["items"]]
    assert commission["commissionId"] in ids
    # The list pins the consent text too: a tester cannot be asked to accept
    # terms the inbox never shows them.
    listed = next(
        o for o in offers.json()["items"] if o["commissionId"] == commission["commissionId"]
    )
    assert listed["consentTerms"]["version"] == 1
    assert "consent" in listed["consentTerms"]["body"].lower()

    detail = tester.get(desk.path(f"/tester/offers/{commission['commissionId']}"))
    assert detail.status_code == 200
    assert detail.json()["consentTerms"]["version"] == 1
    assert "consent" in detail.json()["consentTerms"]["body"].lower()
    # The access credentials are NOT part of the offer: they belong to the claim winner.
    assert "accessNote" not in detail.json()["brief"]

    claimed = _claim(desk, desk.tester_user, commission["commissionId"])
    assert claimed.status_code == 201, claimed.text
    assignment = claimed.json()
    assert assignment["status"] == "ACTIVE"
    assert assignment["briefRevision"] == 1
    assert assignment["consentTermsVersion"] == 1
    assert assignment["brief"]["feeMinor"] == 2000
    # The winner can now read the credentials (T-004's positive side).
    assert assignment["brief"]["accessNote"] == AF21_BRIEF["accessNote"]

    mine = tester.get(desk.path("/tester/assignments"))
    assert any(a["assignmentId"] == assignment["assignmentId"] for a in mine.json()["items"])


def test_t004_race_two_claims_only_the_winner_reads_credentials(desk: Desk) -> None:
    """T-004: two eligible claims race; exactly one wins and owns the credentials."""
    commission = _published_af21(desk)

    def claim_as(user: str) -> int:
        return _claim(desk, user, commission["commissionId"]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim_as, [desk.tester_user, desk.tester2_user]))
    assert sorted(results) == [201, 409], results

    winner_user = desk.tester_user if results[0] == 201 else desk.tester2_user
    loser_user = desk.tester2_user if winner_user == desk.tester_user else desk.tester_user

    winner = desk.client_for(winner_user)
    assignments = winner.get(desk.path("/tester/assignments")).json()["items"]
    assert len(assignments) == 1
    assignment_id = assignments[0]["assignmentId"]
    detail = winner.get(desk.path(f"/tester/assignments/{assignment_id}"))
    assert detail.status_code == 200
    assert detail.json()["brief"]["accessNote"] == AF21_BRIEF["accessNote"]

    loser = desk.client_for(loser_user)
    assert loser.get(desk.path("/tester/assignments")).json()["items"] == []
    # A guessed assignment id yields nothing: not the credentials, not the record.
    assert loser.get(desk.path(f"/tester/assignments/{assignment_id}")).status_code == 404
    # And the offer is gone from the loser's scoped view entirely.
    assert loser.get(desk.path(f"/tester/offers/{commission['commissionId']}")).status_code == 404


def test_t002_pinned_terms_survive_attempted_change(desk: Desk) -> None:
    """T-002: change the fee after acceptance; the assignment still carries 20.00."""
    commission = _published_af21(desk)
    claimed = _claim(desk, desk.tester_user, commission["commissionId"])
    assert claimed.status_code == 201
    assignment = claimed.json()

    agency = desk.client_for(desk.owner)
    # Re-authoring terms on a claimed commission is refused outright.
    changed_fee = dict(AF21_BRIEF)
    changed_fee["feeMinor"] = 3000
    refused = agency.post(
        desk.path(f"/commissions/{commission['commissionId']}/revisions"),
        json={"brief": changed_fee},
    )
    assert refused.status_code == 409

    # The underlying revision row is immutable by trigger, not just by convention.
    with workspace_connection(desk.database_url, desk.workspace_id) as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                "UPDATE td_brief_revision SET fee_minor=3000 WHERE commission_id=%s",
                (commission["commissionId"],),
            )

    # Re-read after the attempts: the pinned brief still says 20.00 USD.
    winner = desk.client_for(desk.tester_user)
    reread = winner.get(desk.path(f"/tester/assignments/{assignment['assignmentId']}"))
    assert reread.json()["brief"]["feeMinor"] == 2000
    assert reread.json()["brief"]["currency"] == "USD"
    assert reread.json()["brief"]["briefHash"] == assignment["briefHash"]


def test_ineligible_and_unenrolled_actors_cannot_claim(desk: Desk) -> None:
    commission = _published_af21(desk)

    # A signed-in person with no roster enrollment sees nothing (scoped visibility).
    outsider = desk.client_for(desk.outsider_user)
    assert outsider.get(desk.path("/tester/offers")).status_code == 404
    assert (
        outsider.post(
            desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
            json={"consentTermsVersion": 1, "consentAccepted": True},
        ).status_code
        == 404
    )

    # Enrolled with the wrong assistive technology: the offer never reaches them.
    agency = desk.client_for(desk.owner)
    nvda_user = desk.reviewer  # reuse an existing account as the wrong-capability tester
    enrolled = agency.post(
        desk.path("/testers"),
        json={
            "userId": nvda_user,
            "displayName": "Riley NVDA",
            "capabilities": {
                "browserName": "Safari",
                "browserVersion": "18",
                "assistiveTechnology": "NVDA",
            },
            "payoutReference": "local-ledger:riley",
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    nvda = desk.client_for(nvda_user)
    assert nvda.get(desk.path("/tester/offers")).json()["items"] == []
    # Enrolled but ineligible: a 409 naming the exact failed term, not a hidden 404.
    refused = nvda.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assert refused.status_code == 409
    assert "assistiveTechnology" in refused.json()["detail"]

    # Right technology, no consent review: refused.
    tester = desk.client_for(desk.tester_user)
    refused2 = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 99, "consentAccepted": True},
    )
    assert refused2.status_code == 409
    refused3 = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": False},
    )
    assert refused3.status_code == 409


def test_offer_expiry_closes_claims(desk: Desk) -> None:
    agency = desk.client_for(desk.owner)
    brief = dict(AF21_BRIEF)
    created = agency.post(
        desk.path("/commissions"),
        json={
            "clientId": desk.client_id,
            "reference": "AF-EXP",
            "offerExpiresAt": "2020-01-01T00:00:00Z",
            "brief": brief,
        },
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))

    tester = desk.client_for(desk.tester_user)
    # Expired offers are surfaced as expired and reject claims.
    refused = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assert refused.status_code in (404, 409)
    listed = tester.get(desk.path("/tester/offers")).json()["items"]
    assert all(o["commissionId"] != commission["commissionId"] for o in listed)


def test_withdrawal_releases_the_claim(desk: Desk) -> None:
    commission = _published_af21(desk)
    claimed = _claim(desk, desk.tester_user, commission["commissionId"])
    assignment_id = claimed.json()["assignmentId"]

    tester = desk.client_for(desk.tester_user)
    withdrawn = tester.post(desk.path(f"/tester/assignments/{assignment_id}/withdraw"))
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"

    # The slot is released: a second eligible tester can now claim.
    claimed2 = _claim(desk, desk.tester2_user, commission["commissionId"])
    assert claimed2.status_code == 201, claimed2.text
