"""Tester Desk compensation: exactly-once payables, dispatch and reconcile (M05 · T-009/T-010)."""

from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.integration

from accessforge_domain import tester_desk as domain  # noqa: E402
from accessforge_persistence import tester_desk as td  # noqa: E402
from accessforge_persistence import workspace_connection  # noqa: E402
from conftest import AF21_BRIEF, Desk  # noqa: E402


def _obs(step: str, result: str = "PASSED") -> dict:
    return {
        "stepId": step,
        "result": result,
        "source": "HUMAN_MANUAL",
        "observedAt": "2026-11-20T10:00:00Z",
    }


def _accepted_assignment(desk: Desk) -> dict:
    """Claim, capture the failing journey, submit, and accept -- returns the payable."""
    agency = desk.client_for(desk.owner)
    created = agency.post(
        desk.path("/commissions"),
        json={"clientId": desk.client_id, "reference": "AF-21", "brief": AF21_BRIEF},
    )
    commission = created.json()
    agency.post(desk.path(f"/commissions/{commission['commissionId']}/publish"))
    tester = desk.client_for(desk.tester_user)
    claimed = tester.post(
        desk.path(f"/tester/offers/{commission['commissionId']}/claim"),
        json={"consentTermsVersion": 1, "consentAccepted": True},
    )
    assignment_id = claimed.json()["assignmentId"]
    tester.put(
        desk.path(f"/tester/assignments/{assignment_id}/draft"),
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
    tester.post(
        desk.path(f"/tester/assignments/{assignment_id}/attachments"),
        json={
            "kind": "ACTION_TRACE",
            "contentType": "application/json",
            "filename": "trace.json",
            "contentBase64": base64.b64encode(b'{"e":1}').decode(),
        },
    )
    submitted = tester.post(desk.path(f"/tester/assignments/{assignment_id}/submit"), json={})
    assert submitted.status_code == 200
    reviewer = desk.client_for(desk.reviewer)
    decision = reviewer.post(
        desk.path(f"/review/assignments/{assignment_id}/decision"),
        json={
            "submissionDigest": submitted.json()["submissionHash"],
            "decision": "ACCEPTED",
            "justification": "complete evidence",
        },
    )
    assert decision.status_code == 200, decision.text
    return {"assignmentId": assignment_id, "commissionId": commission["commissionId"]}


def test_t009_one_acceptance_creates_one_payable(desk: Desk) -> None:
    """T-009 + INV-05: the 20.00 acceptance mints exactly one payable; nothing can mint another."""
    ids = _accepted_assignment(desk)
    finance = desk.client_for(desk.finance)

    payables = finance.get(desk.path("/finance/payables")).json()["items"]
    ours = [p for p in payables if p["assignmentId"] == ids["assignmentId"]]
    assert len(ours) == 1
    payable = ours[0]
    assert payable["amountMinor"] == 2000
    assert payable["currency"] == "USD"
    assert payable["payoutReference"] == "local-ledger:taylor"
    assert payable["status"] == "INTENT"

    # A second "approval" cannot mint another intent -- the decision path is closed and the
    # schema's UNIQUE(assignment_id) backs it regardless.
    reviewer = desk.client_for(desk.reviewer)
    again = reviewer.post(
        desk.path(f"/review/assignments/{ids['assignmentId']}/decision"),
        json={
            "submissionDigest": "0" * 64,
            "decision": "ACCEPTED",
            "justification": "double approval attempt",
        },
    )
    assert again.status_code == 409
    payables_after = finance.get(desk.path("/finance/payables")).json()["items"]
    assert len([p for p in payables_after if p["assignmentId"] == ids["assignmentId"]]) == 1

    # Dispatch through the local ledger adapter settles exactly once.
    results = td.run_payable_dispatch(
        desk.database_url,
        workspace_id=desk.workspace_id,
        adapter=td.LocalLedgerAdapter(),
    )
    ours = [r for r in results if r["assignmentId"] == ids["assignmentId"]]
    assert len(ours) == 1 and ours[0]["status"] == "SETTLED"
    assert ours[0]["providerReference"].startswith("ledger:")

    # A second dispatch run finds nothing to do.
    assert (
        td.run_payable_dispatch(
            desk.database_url,
            workspace_id=desk.workspace_id,
            adapter=td.LocalLedgerAdapter(),
        )
        == []
    )

    # The tester sees their payable lifecycle.
    tester = desk.client_for(desk.tester_user)
    own = tester.get(desk.path(f"/tester/assignments/{ids['assignmentId']}/payable"))
    assert own.json()["payable"]["status"] == "SETTLED"


def test_t010_dispatched_payable_never_dispatches_again(desk: Desk) -> None:
    """T-010: a worker dies after marking DISPATCHED; restart cannot pay twice."""
    ids = _accepted_assignment(desk)
    finance = desk.client_for(desk.finance)
    payable = [
        p
        for p in finance.get(desk.path("/finance/payables")).json()["items"]
        if p["assignmentId"] == ids["assignmentId"]
    ][0]

    # Phase one of the worker commits DISPATCHED; the "process" then dies -- the adapter is
    # never invoked, which is precisely the post-dispatch kill the scenario describes.
    with workspace_connection(desk.database_url, desk.workspace_id) as conn:
        claimed = td.claim_intent_payables(conn, workspace_id=desk.workspace_id)
    assert [c["payableId"] for c in claimed] == [payable["payableId"]]

    class SpyAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def dispatch(self, **kwargs) -> domain.DispatchOutcome:
            self.calls += 1
            return domain.DispatchOutcome(status="SETTLED", provider_reference="spy:1")

    spy = SpyAdapter()
    td.run_payable_dispatch(desk.database_url, workspace_id=desk.workspace_id, adapter=spy)
    assert spy.calls == 0  # nothing in INTENT: the stuck payable is not retried

    stuck = finance.get(desk.path(f"/finance/payables/{payable['payableId']}")).json()
    assert stuck["status"] == "DISPATCHED"
    assert stuck["dispatchAttempts"] == 1

    # Reconcile requires the provider reference and the exact amount.
    wrong_amount = finance.post(
        desk.path(f"/finance/payables/{payable['payableId']}/reconcile"),
        json={"providerReference": "ledger:local-1", "amountMinor": 2001},
    )
    assert wrong_amount.status_code == 409

    settled = finance.post(
        desk.path(f"/finance/payables/{payable['payableId']}/reconcile"),
        json={"providerReference": "ledger:local-1", "amountMinor": 2000},
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "SETTLED"
    assert settled.json()["providerReference"] == "ledger:local-1"

    # Settled is terminal: reconcile cannot move it again.
    again = finance.post(
        desk.path(f"/finance/payables/{payable['payableId']}/reconcile"),
        json={"providerReference": "ledger:local-1", "amountMinor": 2000},
    )
    assert again.status_code == 409


def test_unknown_outcome_reconciles_by_original_reference(desk: Desk) -> None:
    """An adapter that reports uncertainty leaves UNKNOWN, reconcilable by its own reference."""
    ids = _accepted_assignment(desk)

    class UncertainAdapter:
        def dispatch(self, **kwargs) -> domain.DispatchOutcome:
            return domain.DispatchOutcome(
                status="UNKNOWN", provider_reference="provider:ambiguous-1"
            )

    td.run_payable_dispatch(
        desk.database_url,
        workspace_id=desk.workspace_id,
        adapter=UncertainAdapter(),
    )
    finance = desk.client_for(desk.finance)
    payable = [
        p
        for p in finance.get(desk.path("/finance/payables")).json()["items"]
        if p["assignmentId"] == ids["assignmentId"]
    ][0]
    assert payable["status"] == "UNKNOWN"

    # The recorded provider reference is the reconcile key: a different one is refused.
    wrong_ref = finance.post(
        desk.path(f"/finance/payables/{payable['payableId']}/reconcile"),
        json={"providerReference": "provider:other", "amountMinor": 2000},
    )
    assert wrong_ref.status_code == 409

    ok = finance.post(
        desk.path(f"/finance/payables/{payable['payableId']}/reconcile"),
        json={"providerReference": "provider:ambiguous-1", "amountMinor": 2000},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "SETTLED"


def test_payables_are_not_visible_to_reviewers_or_testers_of_other_assignments(
    desk: Desk,
) -> None:
    _accepted_assignment(desk)
    reviewer = desk.client_for(desk.reviewer)
    assert reviewer.get(desk.path("/finance/payables")).status_code in (403, 404)
    other_tester = desk.client_for(desk.tester2_user)
    assert other_tester.get(desk.path("/finance/payables")).status_code in (403, 404)
