"""Synthetic retained records for CI; these are not physical reader execution proof."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.runtime_evidence import interpret

CONTEXT = {"workspace_id": "workspace", "run_id": "run", "lease_id": "lease", "epoch": 1}


def fixture() -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for ordinal in (1, 2):
        payload = {
            "workspaceId": "workspace",
            "runId": "run",
            "leaseId": "lease",
            "leaseEpoch": 1,
            "meaning": "BUILD_RECEIPT_CONTEXT_NOT_CANONICAL_EXECUTION_EVIDENCE",
            "observation": {
                "artifactDigest": "a" * 64,
                "observedAt": "2026-09-13T00:00:01Z",
                "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
            },
        }
        fingerprint = digest(payload)
        receipt = {
            "receipt": payload,
            "receiptDigest": fingerprint,
            "receiptId": str(
                uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint)
            ),
        }
        for kind, offset in (("ACTION_INTENT", 0), ("ACTION_RESULT", 2)):
            actions.append(
                {
                    "eventType": kind,
                    "sequence": ordinal * 3 + offset,
                    "payload": {
                        "serviceIdentity": "SUPERVISOR",
                        "sourceRecord": {"actionId": str(ordinal), "sequence": ordinal},
                    },
                }
            )
        reports.append(
            {
                "eventType": "PREFLIGHT_RESULT",
                "sequence": ordinal * 3 + 1,
                "payload": {
                    "serviceIdentity": "SUPERVISOR",
                    "provenance": "RUNTIME_PROBE_REPORT",
                    "buildArtifactReceipt": receipt,
                    "sourceRecord": {
                        "actionId": str(ordinal),
                        "actionSequence": ordinal,
                        "capturedAtUtc": "2026-09-13T00:00:02Z",
                        "checks": dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, "TRUE"),
                    },
                },
            }
        )
    return {"ACTION_TRACE": {"records": actions}, "PREFLIGHT_RECORD": {"records": reports}}


def test_complete_runtime_reports_use_measurement_not_sealed_build() -> None:
    result = interpret(fixture(), CONTEXT)
    assert result.preflight_passed and result.observed_build == "a" * 64
    assert not result.reasons


@pytest.mark.parametrize("missing", ["all", "one", "measurement", "unknown", "build-unknown"])
def test_missing_or_unknown_evidence_does_not_become_complete(missing: str) -> None:
    snapshots = fixture()
    reports = snapshots["PREFLIGHT_RECORD"]["records"]
    if missing == "all":
        for report in reports:
            report["payload"]["provenance"] = "CONTROL_PLANE_RECEIPT"
    elif missing == "one":
        reports.pop()
    elif missing == "measurement":
        del reports[0]["payload"]["buildArtifactReceipt"]
    else:
        check = (
            "BUILD_IDENTITY_MATCHES_MANIFEST"
            if missing == "build-unknown"
            else next(
                name
                for name in REQUIRED_PREFLIGHT_CHECKS
                if name != "BUILD_IDENTITY_MATCHES_MANIFEST"
            )
        )
        reports[0]["payload"]["sourceRecord"]["checks"][check] = "UNKNOWN"
    result = interpret(snapshots, CONTEXT)
    assert result.preflight_passed is (missing == "measurement")
    assert result.observed_build == ("a" * 64 if missing == "unknown" else None)
    assert result.reasons


@pytest.mark.parametrize(
    "fault", ["digest", "foreign", "stale", "future", "order", "bool", "duplicate"]
)
def test_untrusted_or_misordered_runtime_records_refused(fault: str) -> None:
    snapshots = fixture()
    reports = snapshots["PREFLIGHT_RECORD"]["records"]
    report = reports[0]
    receipt = report["payload"]["buildArtifactReceipt"]
    if fault in {"digest", "foreign"}:
        receipt["receipt"]["leaseId"] = "other-lease"
        if fault == "foreign":
            fingerprint = digest(receipt["receipt"])
            receipt["receiptDigest"] = fingerprint
            receipt["receiptId"] = str(
                uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint)
            )
    elif fault in {"stale", "future"}:
        report["payload"]["sourceRecord"]["capturedAtUtc"] = (
            "2026-09-13T00:00:12Z" if fault == "stale" else "2026-09-13T00:00:00Z"
        )
    elif fault == "order":
        report["sequence"] = 5
    elif fault == "bool":
        report["payload"]["sourceRecord"]["actionSequence"] = True
    else:
        reports.append(report)
    with pytest.raises(Refused):
        interpret(snapshots, CONTEXT)
