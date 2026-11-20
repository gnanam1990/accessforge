"""Tester Desk end-to-end pilot: the AF-21 reference scene through normal API entry points (M08).

Honest scope note: this drives the whole product journey locally with fixture credentials and a
local ledger adapter. What it deliberately does NOT claim: an independent human tester, a real
VoiceOver+Safari run, or a live payout rail -- those are recorded external gates.
"""

from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.integration

from accessforge_persistence import tester_desk as td  # noqa: E402
from accessforge_persistence import workspace_connection  # noqa: E402
from conftest import AF21_BRIEF, Desk  # noqa: E402


def test_pilot_af21_full_journey(desk: Desk) -> None:
    agency = desk.client_for(desk.owner)
    tester = desk.client_for(desk.tester_user)
    reviewer = desk.client_for(desk.reviewer)
    finance = desk.client_for(desk.finance)

    # 1. Agency commissions AF-21: r18, five-step checkout, 20.00 USD.
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF,
              "defectKey": "focus-trap-s4"},
    )
    assert created.status_code == 201, created.text
    commission = created.json()
    published = agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    assert published.status_code == 200

    # 2. Eligible tester sees the offer, accepts consent, claims exclusively.
    offers = tester.get(desk.path("/tester/offers"))
    assert offers.status_code == 200
    offer = [o for o in offers.json()["items"] if o["reference"] == "AF-21"][0]
    assert offer["brief"]["feeMinor"] == 2000
    claimed = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assert claimed.status_code == 201, claimed.text
    aid = claimed.json()["assignmentId"]
    # Credentials are delivered only to the winner.
    assert "desk-demo" in str(claimed.json()["brief"].get("accessNote", ""))

    # 3. Tester performs the journey: step four traps focus, step five unreachable.
    obs = [
        {"stepId": s, "result": r, "notes": n, "source": "HUMAN_MANUAL",
         "observedAt": "2026-11-20T10:00:00Z"}
        for s, r, n in [
            ("s1", "PASSED", "opened fine"),
            ("s2", "PASSED", "contact form ok"),
            ("s3", "PASSED", "shipping form ok"),
            ("s4", "FAILED", "keyboard focus trapped inside the shipping summary dialog"),
            ("s5", "UNREACHABLE", "payment step cannot be reached after the trap"),
        ]
    ]
    saved = tester.put(
        desk.path(f"/tester/assignments/{aid}/draft"),
        json={"observations": obs, "baseRevision": 0},
    )
    assert saved.status_code == 200

    # Restart/recovery: a brand-new session recovers the exact draft (NFR-01).
    resumed = desk.client_for(desk.tester_user).get(
        desk.path(f"/tester/assignments/{aid}/draft"))
    assert len(resumed.json()["observations"]) == 5

    trace = b'{"events":[{"step":"s4","action":"focus","result":"trap"}]}'
    uploaded = tester.post(
        desk.path(f"/tester/assignments/{aid}/attachments"),
        json={"kind": "ACTION_TRACE", "contentType": "application/json",
              "filename": "focus-trace.json",
              "contentBase64": base64.b64encode(trace).decode()},
    )
    assert uploaded.status_code == 201

    # 4. Submit sealed revision; task result FAILED but evidence complete.
    submitted = tester.post(desk.path(f"/tester/assignments/{aid}/submit"), json={})
    assert submitted.status_code == 200
    assert submitted.json()["taskResult"] == "FAILED"

    # 5. Agency reviewer accepts the evidence (failed task still earns acceptance).
    decision = reviewer.post(
        desk.path(f"/review/assignments/{aid}/decision"),
        json={"submissionDigest": submitted.json()["submissionHash"],
              "decision": "ACCEPTED",
              "justification": "complete, readable evidence of the s4 focus trap"},
    )
    assert decision.status_code == 200

    # 6. Payable minted exactly once -- then the "payout adapter is disconnected":
    #    no dispatch runs, so the payable stays INTENT while accepted work remains visible.
    payable = [
        p for p in finance.get(desk.path("/finance/payables")).json()["items"]
        if p["assignmentId"] == aid
    ][0]
    assert payable["status"] == "INTENT"
    assert payable["amountMinor"] == 2000

    report = agency.post(desk.path("/reports/generate"), json={"assignmentId": aid})
    assert report.status_code == 201, report.text
    report_id = report.json()["reportId"]
    granted = agency.post(
        desk.path(f"/clients/{desk.client_id}/viewers"), json={"userId": desk.client_user})
    assert granted.status_code == 201
    client_view = desk.client_for(desk.client_user).get(
        desk.path(f"/client/reports/{report_id}"))
    assert client_view.status_code == 200
    assert client_view.json()["body"]["taskResult"] == "FAILED"
    # The payable is still pending -- disconnect left accepted findings fully available (T-016).
    still = finance.get(desk.path(f"/finance/payables/{payable['payableId']}")).json()
    assert still["status"] == "INTENT"

    # 7. Adapter reconnects: dispatch settles exactly once through the local ledger.
    results = td.run_payable_dispatch(
        desk.database_url, workspace_id=desk.workspace_id,
        adapter=td.LocalLedgerAdapter(),
    )
    ours = [r for r in results if r.get("assignmentId") == aid]
    assert len(ours) == 1 and ours[0]["status"] == "SETTLED"
    assert td.run_payable_dispatch(
        desk.database_url, workspace_id=desk.workspace_id,
        adapter=td.LocalLedgerAdapter()) == []

    # 8. Follow-up release r19 gets a fresh linked retest -- nothing inherits r18's verdict.
    r19 = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-22",
              "brief": dict(AF21_BRIEF, releaseLabel="r19", releaseDigest="b" * 64),
              "defectKey": "focus-trap-s4",
              "predecessorCommissionId": commission["commissionId"]},
    )
    assert r19.status_code == 201
    agency.post(desk.path(f"/commissions/{r19.json()['commissionId']}/publish"))
    comparison = agency.get(
        desk.path(f"/commissions/{commission['commissionId']}/compare"),
        params={"against": r19.json()["commissionId"]},
    ).json()
    assert comparison["base"]["taskResult"] == "FAILED"
    assert comparison["candidate"]["taskResult"] is None
    assert comparison["link"] == "SAME_DEFECT_RETEST"
